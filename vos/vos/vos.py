# ***********************************************************************
# ******************  CANADIAN ASTRONOMY DATA CENTRE  *******************
# *************  CENTRE CANADIEN DE DONNÉES ASTRONOMIQUES  **************
#
#  (c) 2025.                            (c) 2025.
#  Government of Canada                 Gouvernement du Canada
#  National Research Council            Conseil national de recherches
#  Ottawa, Canada, K1A 0R6              Ottawa, Canada, K1A 0R6
#  All rights reserved                  Tous droits réservés
#
#  NRC disclaims any warranties,        Le CNRC dénie toute garantie
#  expressed, implied, or               énoncée, implicite ou légale,
#  statutory, of any kind with          de quelque nature que ce
#  respect to the software,             soit, concernant le logiciel,
#  including without limitation         y compris sans restriction
#  any warranty of merchantability      toute garantie de valeur
#  or fitness for a particular          marchande ou de pertinence
#  purpose. NRC shall not be            pour un usage particulier.
#  liable in any event for any          Le CNRC ne pourra en aucun cas
#  damages, whether direct or           être tenu responsable de tout
#  indirect, special or general,        dommage, direct ou indirect,
#  consequential or incidental,         particulier ou général,
#  arising from the use of the          accessoire ou fortuit, résultant
#  software.  Neither the name          de l'utilisation du logiciel. Ni
#  of the National Research             le nom du Conseil National de
#  Council of Canada nor the            Recherches du Canada ni les noms
#  names of its contributors may        de ses  participants ne peuvent
#  be used to endorse or promote        être utilisés pour approuver ou
#  products derived from this           promouvoir les produits dérivés
#  software without specific prior      de ce logiciel sans autorisation
#  written permission.                  préalable et particulière
#                                       par écrit.
#
#  This file is part of the             Ce fichier fait partie du projet
#  OpenCADC project.                    OpenCADC.
#
#  OpenCADC is free software:           OpenCADC est un logiciel libre ;
#  you can redistribute it and/or       vous pouvez le redistribuer ou le
#  modify it under the terms of         modifier suivant les termes de
#  the GNU Affero General Public        la “GNU Affero General Public
#  License as published by the          License” telle que publiée
#  Free Software Foundation,            par la Free Software Foundation
#  either version 3 of the              : soit la version 3 de cette
#  License, or (at your option)         licence, soit (à votre gré)
#  any later version.                   toute version ultérieure.
#
#  OpenCADC is distributed in the       OpenCADC est distribué
#  hope that it will be useful,         dans l’espoir qu’il vous
#  but WITHOUT ANY WARRANTY;            sera utile, mais SANS AUCUNE
#  without even the implied             GARANTIE : sans même la garantie
#  warranty of MERCHANTABILITY          implicite de COMMERCIALISABILITÉ
#  or FITNESS FOR A PARTICULAR          ni d’ADÉQUATION À UN OBJECTIF
#  PURPOSE.  See the GNU Affero         PARTICULIER. Consultez la Licence
#  General Public License for           Générale Publique GNU Affero
#  more details.                        pour plus de détails.
#
#  You should have received             Vous devriez avoir reçu une
#  a copy of the GNU Affero             copie de la Licence Générale
#  General Public License along         Publique GNU Affero avec
#  with OpenCADC.  If not, see          OpenCADC ; si ce n’est
#  <http://www.gnu.org/licenses/>.      pas le cas, consultez :
#                                       <http://www.gnu.org/licenses/>.
#
#
# ***********************************************************************


"""A set of Python Classes for connecting to and interacting with a VOSpace
   service.

   Connections to VOSpace are made using a SSL X509 certificat which is
   stored in a .pem file.
"""

import warnings
import copy
import errno
from datetime import datetime
import fnmatch
from enum import Enum
import hashlib

try:
    from cStringIO import StringIO
except ImportError:
    from io import StringIO
import requests
from requests.exceptions import HTTPError
import html2text
import logging
import mimetypes
import os
import re
import stat
import sys
import time
import urllib
from xml.etree import ElementTree
from copy import deepcopy
from .node_cache import NodeCache
from .vosconfig import vos_config

try:
    from .version import version
except ImportError:
    version = "unknown"
from cadcutils import net, exceptions, util
from . import md5_cache

from urllib.parse import urlparse, parse_qs
logger = logging.getLogger('vos')

if sys.version_info[1] > 6:
    logger.addHandler(logging.NullHandler())

# ch = logging.StreamHandler()
# ch.setLevel(logging.DEBUG)
# logger.addHandler(ch)

BUFSIZE = 8388608  # Size of read/write buffer
MAX_RETRY_DELAY = 128  # maximum delay between retries
# start delay between retries when Try_After not sent by server.
DEFAULT_RETRY_DELAY = 30
MAX_RETRY_TIME = 900  # maximum time for retries before giving up...
MAX_INTERMTTENT_RETRIES = 3

VOSPACE_ARCHIVE = os.getenv("VOSPACE_ARCHIVE", "vospace")
HEADER_DELEG_TOKEN = 'X-CADC-DelegationToken'
HEADER_CONTENT_LENGTH = 'X-CADC-Content-Length'
HEADER_PARTIAL_READ = 'X-CADC-Partial-Read'

CADC_GMS_PREFIX = "ivo://cadc.nrc.ca/gms?"

VO_PROPERTY_URI_ISLOCKED = 'ivo://cadc.nrc.ca/vospace/core#islocked'
VO_VIEW_DEFAULT = 'ivo://ivoa.net/vospace/core#defaultview'
VO_PROPERTY_LENGTH = 'ivo://ivoa.net/vospace/core#length'
VO_PROPERTY_DATE = 'ivo://ivoa.net/vospace/core#date'
VO_PROPERTY_MD5 = 'ivo://ivoa.net/vospace/core#MD5'
# CADC specific views
VO_CADC_VIEW_URI = 'ivo://cadc.nrc.ca/vospace/view'

SSO_SECURITY_METHODS = {
    'tls-with-certificate': 'ivo://ivoa.net/sso#tls-with-certificate',
    'cookie': 'ivo://ivoa.net/sso#cookie',
    'token': 'vos://cadc.nrc.ca~vospace/CADC/std/Auth#token-1.0'
}

SUPPORTED_SERVER_VERSIONS = {'vault': '1.1',
                             'cavern': '1.0',
                             'storage-inventory/minoc': '1.0'}

# this should one day go into its own uws library
UWS_NSMAP = {'uws': 'http://www.ivoa.net/xml/UWS/v1.0',
             'xlink': 'http://www.w3.org/1999/xlink'}


# sorting-related uris
class SortNodeProperty(Enum):
    """ URIs of node properties used for sorting"""
    LENGTH = VO_PROPERTY_LENGTH
    DATE = VO_PROPERTY_DATE


CADC_VO_VIEWS = {'data': '{}#data'.format(VO_CADC_VIEW_URI),
                 'manifest': '{}#manifest'.format(VO_CADC_VIEW_URI),
                 'rss': '{}#rss'.format(VO_CADC_VIEW_URI),
                 'cutout': '{}#cutout'.format(VO_CADC_VIEW_URI)}

# md5sum of a size zero file
ZERO_MD5 = 'd41d8cd98f00b204e9800998ecf8427e'


# Pattern matching in filenames to extract out the RA/DEC/RADIUS part
FILENAME_PATTERN_MAGIC = re.compile(
    r'^(?P<filename>[/_\-=+!,;:@&*$.\w~]*)'  # legal filename string
    r'(?P<cutout>'  # Look for a cutout part
    r'(?P<pix>(\[\d*:?\d*\])?'
    r'(\[[+-]?\*?\d*:?[+-]?\d*,?[+-]?\*?\d*:?[+-]?\d*\]))'  # pixel
    r'|'  # OR
    r'(?P<wcs>'  # possible wcs cutout
    r'\((?P<ra>[+]?\d*(\.\d*)?),'  # ra part
    r'(?P<dec>[\-+]?\d*(\.\d*)?),'  # dec part
    r'(?P<rad>\d*(\.\d*)?)\))'  # radius of cutout
    r')?$'
    )
MAGIC_GLOB_CHECK = re.compile('[*?[]')


logging.getLogger('requests').setLevel(logging.ERROR)


def convert_vospace_time_to_seconds(str_date):
    """A convenience method that takes a string from a vospace time field (UTC)
    and converts it to seconds since epoch local time.

    :param str_date: string to parse into a VOSpace time
    :type str_date: unicode
    :return: A datetime object for the provided string date
    :rtype: datetime
    """
    right = str_date.rfind(":") + 3
    mtime = time.mktime(time.strptime(str_date[0:right], '%Y-%m-%dT%H:%M:%S'))
    return mtime - round((datetime.utcnow() - datetime.now()).total_seconds())


class Connection(object):
    """Class to hold and act on the X509 certificate"""

    def __init__(self, vospace_certfile=None, vospace_token=None,
                 http_debug=False,
                 resource_id=None, insecure=False):
        """Setup the Certificate for later usage

        vospace_certfile -- where to store the certificate, if None then
                         ${HOME}/.ssl or a temporary filename
        vospace_token -- token string (alternative to vospace_certfile)
        http_debug -- set True to generate debug statements (Deprecated)
        resource_id -- The resource ID of the vospace service. Defaults to
        CADC vos.
        insecure -- Allow insecure server connections when using SSL.

        If the user supplies an empty vospace_certificate, the connection
        will be 'anonymous'. If no certificate or token are provided, and
        attempt to find user/password combination in the .netrc file is made
        before the connection is downgraded to 'anonymous'
        """
        if http_debug is not False:
            warnings.warn(
                "Connection object no longer uses http_debug setting.",
                DeprecationWarning)
        self.vo_token = None
        session_headers = None
        self.resource_id = resource_id
        if vospace_token is not None:
            session_headers = {HEADER_DELEG_TOKEN: vospace_token}
            self.subject = net.Subject()
            self.vo_token = vospace_token
        else:
            cert = vospace_certfile
            if cert is not None:
                if len(cert) == 0:
                    logger.debug('Anonymous access (certfile=Anonymous)')
                    self.subject = net.Subject()
                elif not os.access(vospace_certfile, os.F_OK):
                    logger.warning(
                        "Could not access certificate at {0}.".format(cert))
                    cert = None
                else:
                    logger.debug(
                        'Authenticate with cert {}'.format(vospace_certfile))
                    self.subject = net.Subject(certificate=vospace_certfile)

            if cert is None:
                if os.access(os.path.join(os.environ['HOME'], ".netrc"),
                             os.F_OK):
                    logger.debug(
                        ('Authenticate with user/password '
                         'from $HOME/.netrc file'))
                    self.subject = net.Subject(netrc=True)
                else:
                    logger.warning(
                        ('No valid authentication found. '
                         'Reverting to anonymous.'))
                    self.subject = net.Subject()
        host = os.getenv('VOSPACE_WEBSERVICE', os.getenv('LOCAL_VOSPACE_WEBSERVICE', None))
        self.ws_client = net.BaseWsClient(resource_id, self.subject,
                                          'vos/' + version,
                                          host=host,
                                          session_headers=session_headers,
                                          insecure=insecure,
                                          server_versions=SUPPORTED_SERVER_VERSIONS)
        EndPoints.subject = self.subject

    @property
    def session(self):
        return self.ws_client._get_session()

    def get_connection(self, url=None):
        """Create an HTTPSConnection object and return.  Uses the client
        certificate if None given.

        :param url: a VOSpace uri
        """
        if url is not None:
            raise OSError(errno.ENOSYS,
                          "Connections are no longer set per URL.")
        return self.ws_client


class Node(object):
    """A VOSpace node"""

    IVOAURL = 'ivo://ivoa.net/vospace/core'
    VOSNS = 'http://www.ivoa.net/xml/VOSpace/v2.0'
    VOSVERSION = '2.1'
    XSINS = 'http://www.w3.org/2001/XMLSchema-instance'
    TYPE = '{{{}}}type'.format(XSINS)
    NODES = '{{{}}}nodes'.format(VOSNS)
    NODE = '{{{}}}node'.format(VOSNS)
    PROTOCOL = '{{{}}}protocol'.format(VOSNS)
    PROPERTIES = '{{{}}}properties'.format(VOSNS)
    PROPERTY = '{{{}}}property'.format(VOSNS)
    ACCEPTS = '{{{}}}accepts'.format(VOSNS)
    PROVIDES = '{{{}}}provides'.format(VOSNS)
    ENDPOINT = '{{{}}}endpoint'.format(VOSNS)
    TARGET = '{{{}}}target'.format(VOSNS)
    DATA_NODE = 'vos:DataNode'
    LINK_NODE = 'vos:LinkNode'
    CONTAINER_NODE = 'vos:ContainerNode'

    def __init__(self, node, node_type=None, properties=None, subnodes=None):
        """Create a Node object based on the DOM passed to the init method

        if node is a string then create a node named node of nodeType with
        properties

        :param node: the name of the node to create or a string representing
        that node
        """
        self.uri = None
        self.name = None
        self.target = None
        self.groupread = None
        self.groupwrite = None
        self.is_public = None
        self.type = None
        self.props = {}
        self.attr = {}
        self.xattr = {}
        self._node_list = None
        self._endpoints = None

        if not subnodes:
            subnodes = []
        if not properties:
            properties = {}

        if node_type is None:
            node_type = Node.DATA_NODE

        if isinstance(node, bytes):
            node = ElementTree.fromstring(node)

        if isinstance(node, str):
            node = self.create(node, node_type, properties, subnodes=subnodes)

        if node is None:
            raise LookupError("no node found or created?")

        self.node = node
        self.node.set('xmlns:vos', self.VOSNS)
        self.update()

    def __eq__(self, node):
        if not isinstance(node, Node):
            return False

        return self.props == node.props

    def update(self):
        """Update the convience links of this node as we update the xml file"""

        self.type = self.node.get(Node.TYPE)
        if self.type is None:
            # logger.debug("Node type unknown, no node created")
            return None
        if self.type == "vos:LinkNode":
            self.target = self.node.findtext(Node.TARGET)

        self.uri = self.node.get('uri')

        self.name = os.path.basename(self.uri)
        for propertiesNode in self.node.findall(Node.PROPERTIES):
            self.set_props(propertiesNode)
        self.is_public = False
        if self.props.get('ispublic', 'false') == 'true':
            self.is_public = True
        logger.debug(
            "{0} {1} -> {2}".format(self.uri, VO_PROPERTY_URI_ISLOCKED,
                                    self.props))
        self.groupwrite = self.props.get('groupwrite', '')
        self.groupread = self.props.get('groupread', '')
        logger.debug("Setting file attributes via setattr")
        self.setattr()
        logger.debug("Setting file x-attributes via setxattr")
        self.setxattr()

    def set_property(self, key, value):
        """Create a key/value pair Node.PROPERTY element.

        :param key: the property key
        :param value: the property value
        """
        properties = self.node.find(Node.PROPERTIES)
        uri = '{}#{}'.format(Node.IVOAURL, key)
        ElementTree.SubElement(properties, Node.PROPERTY,
                               attrib={'uri': uri,
                                       'readOnly': 'false'}).text = value

    def __str__(self):
        """Convert the Node to a string representation of the Node"""

        class Dummy(object):
            pass

        data = []
        file_handle = Dummy()
        file_handle.write = data.append
        ElementTree.ElementTree(self.node).write(file_handle, encoding='UTF-8')
        # concatenate and decode the string
        return b''.join(data).decode('UTF-8')

    def setattr(self, attr=None):
        """return / augment a dictionary of attributes associated with the Node

        These attributes are determined from the node on VOSpace.
        :param attr: the  dictionary that holds the attributes
        """
        if not attr:
            attr = {}
        # Get the flags for file mode settings.

        self.attr = {}

        # Only one date provided by VOSpace, so use this as all possible dates.

        access_time = time.time()
        if not self.props.get('date', None):
            modified_time = access_time
        else:
            # mktime is expecting a localtime but we're sending a UT date, so
            # some correction will be needed
            modified_time = convert_vospace_time_to_seconds(
                self.props.get('date'))

        self.attr['st_ctime'] = attr.get('st_ctime', modified_time)
        self.attr['st_mtime'] = attr.get('st_mtime', modified_time)
        self.attr['st_atime'] = access_time

        # set the MODE by or'ing together all flags from stat
        st_mode = 0
        st_nlink = 1
        if self.type == 'vos:ContainerNode':
            st_mode |= stat.S_IFDIR
            st_nlink = max(2, len(self.get_info_list()) + 2)
            # if getInfoList length is < 0 we have a problem elsewhere, so
            # above hack solves that problem.
        elif self.type == 'vos:LinkNode':
            st_mode |= stat.S_IFLNK
        else:
            st_mode |= stat.S_IFREG
        self.attr['st_nlink'] = st_nlink

        # Set the OWNER permissions: all vospace Nodes have read/write/execute
        # by owner
        st_mode |= stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR

        # Set the GROUP permissions
        if self.props.get('groupwrite', "NONE") != "NONE":
            st_mode |= stat.S_IWGRP
        if self.props.get('groupread', "NONE") != "NONE":
            st_mode |= stat.S_IRGRP
            st_mode |= stat.S_IXGRP

        # Set the OTHER permissions
        if self.props.get('ispublic', 'false') == 'true':
            # If you can read the file then you can execute too.
            # Public does NOT mean writeable.  EVER
            st_mode |= stat.S_IROTH | stat.S_IXOTH

        self.attr['st_mode'] = attr.get('st_mode', st_mode)

        # We set the owner and group bits to be those of the currently running
        # process. This is a hack since we don't have an easy way to figure
        # these out.
        # TODO Come up with a better approach to uid setting
        self.attr['st_uid'] = attr.get('st_uid', os.getuid())
        self.attr['st_gid'] = attr.get('st_uid', os.getgid())

        st_size = int(self.props.get('length', 0))
        self.attr['st_size'] = st_size > 0 and st_size or 0

        self.attr['st_blocks'] = self.attr['st_size'] // 512

    def setxattr(self, attrs=None):
        """Initialize the extended attributes using the Node properties that
        are not part of the core set.

        :param attrs: An input list of attributes being sent from an external
        source, not supported.
        """
        if attrs is not None:
            raise OSError(
                errno.ENOSYS,
                "No externally set extended Attributes for vofs yet.")

        for key in self.props:
            if key in Client.vosProperties:
                continue
            self.xattr[key] = self.props[key]

        return

    def chwgrp(self, group):
        """Set the groupwrite value to group for this node

        :param group: the uri of he group to give write access to.
        :type group: unicode
        """
        logger.debug("Setting groups to: {0}".format(group))
        if group is not None and len(group.split()) > 3:
            raise AttributeError(
                "Exceeded max of 4 write groups: {0}<-".format(group.split()))
        self.groupwrite = group
        return self.change_prop('groupwrite', group)

    def chrgrp(self, group):
        """Set the groupread value to group for this node

        :param group: the uri of the group to give read access to.
        :type group: unicode
        """
        if group is not None and len(group.split()) > 3:
            raise AttributeError(
                "Exceeded max of 4 read groups: {0}<-".format(group))

        self.groupread = group
        return self.change_prop('groupread', group)

    def set_public(self, value):
        """
        :param value: should the is_public flag be set? (true/false)

        :type value: unicode
        """
        return self.change_prop('ispublic', value)

    @staticmethod
    def fix_prop(prop):
        """Check if prop is a well formed uri and if not then make into one

        :param prop: the  property to expand into a  IVOA uri value for a
        property.
        :rtype unicode
        """
        if prop in ['title',
                    'creator',
                    'subject',
                    'description',
                    'publisher',
                    'contributer',
                    'date',
                    'type',
                    'format',
                    'identifier',
                    'source',
                    'language',
                    'relation',
                    'coverage',
                    'rights',
                    'availableSpace',
                    'groupread',
                    'groupwrite',
                    'publicread',
                    'quota',
                    'length',
                    'MD5',
                    'mtime',
                    'ctime',
                    'ispublic']:
            prop = Node.IVOAURL + "#" + prop

        return prop

    @staticmethod
    def set_prop():
        """Build the XML for a given node"""
        raise NotImplementedError('No set prop.')

    def change_prop(self, key, value):
        """Change the node property 'key' to 'value'.

        :param key: The property key to update
        :type key: unicode
        :param value: The value to give that property.
        :type value: unicode,None
        :return True/False depending on if the property value was updated.
        """
        # TODO split into 'set' and 'delete'
        uri = self.fix_prop(key)
        changed = False
        found = False
        properties = self.node.findall(Node.PROPERTIES)
        for props in properties:
            for prop in props.findall(Node.PROPERTY):
                if uri != prop.attrib.get('uri', None):
                    continue
                found = True
                if getattr(prop, 'text') == value:
                    break
                changed = True
                if value is None:
                    # this is actually a delete property
                    prop.attrib['xsi:nil'] = 'true'
                    prop.attrib["xmlns:xsi"] = Node.XSINS
                    prop.text = ""
                    self.props[self.get_prop_name(uri)] = None
                else:
                    prop.text = value
            if found:
                return changed
        # must not have had this kind of property already, so set value
        property_node = ElementTree.SubElement(properties[0], Node.PROPERTY)
        property_node.attrib['readOnly'] = "false"
        property_node.attrib['uri'] = uri
        if value is not None:
            property_node.text = value
        else:
            property_node.attrib['xsi:nil'] = 'true'
        self.props[self.get_prop_name(uri)] = value
        return changed

    def chmod(self, mode):
        """Set the MODE of this Node...

        translates unix style MODE to voSpace and updates the properties...

        This function is quite limited.  We can make a file publicly
        readable and we can turn on/off group read/write permissions,
        that's all.

        :param mode: a stat MODE bit
        """

        changed = 0

        if mode & stat.S_IROTH:
            changed += self.set_public('true')
        else:
            changed += self.set_public('false')

        if mode & stat.S_IRGRP:
            changed += self.chrgrp(self.groupread)
        else:
            changed += self.chrgrp('')

        if mode & stat.S_IWGRP:
            changed += self.chwgrp(self.groupwrite)
        else:
            changed += self.chwgrp('')
        return changed > 0

    def create(self, uri, node_type="vos:DataNode", properties=None,
               subnodes=None):
        """Build the XML needed to represent a VOSpace node returns an
        ElementTree representation of the XML

        :param uri: The URI for this node.
        :type uri: str
        :param node_type: the type of VOSpace node, likely one of
        vos:DataNode, vos:ContainerNode, vos:LinkNode
        :type node_type: unicode
        :param properties:  a dictionary of the node properties, keys should
        be single words from the IVOA list
        :type properties: dict
        :param subnodes: Any children to attach to this node, only valid
        for vos:ContainerNode
        :type subnodes: [Node]
        """
        if not subnodes:
            subnodes = []
        elif node_type != 'vos:ContainerNode':
            raise ValueError("Only Container Nodes can have subnodes")

        if not properties:
            properties = {}

        # Build the root node called 'node'
        node = ElementTree.Element("node")
        node.attrib["xmlns"] = Node.VOSNS
        node.attrib["xmlns:vos"] = Node.VOSNS
        node.attrib[Node.TYPE] = node_type
        node.attrib["uri"] = uri

        # create a properties section
        if ('type' not in properties) and (mimetypes.guess_type(uri)[0]):
            properties['type'] = mimetypes.guess_type(uri)[0]
        properties_node = ElementTree.SubElement(node, Node.PROPERTIES)
        for prop in properties.keys():
            property_node = ElementTree.SubElement(properties_node,
                                                   Node.PROPERTY)
            property_node.attrib['readOnly'] = "false"
            property_node.attrib["uri"] = self.fix_prop(prop)
            if properties[prop] is None:
                # Setting the property value to None indicates that this is
                # actually a delete
                property_node.attrib['xsi:nil'] = 'true'
                property_node.attrib["xmlns:xsi"] = Node.XSINS
                property_node.text = ""
            elif len(str(properties[prop])) > 0:
                property_node.text = properties[prop]

        # That's it for link nodes...
        if node_type == "vos:LinkNode":
            return node

        # create accepts
        accepts = ElementTree.SubElement(node, Node.ACCEPTS)

        ElementTree.SubElement(accepts, "view").attrib['uri'] = VO_VIEW_DEFAULT

        provides = ElementTree.SubElement(node, Node.PROVIDES)
        ElementTree.SubElement(provides, "view").attrib[
            'uri'] = VO_VIEW_DEFAULT
        ElementTree.SubElement(provides, "view").attrib['uri'] = CADC_VO_VIEWS[
            'rss']

        # Only DataNode can have a dataview...
        if node_type == "vos:DataNode":
            ElementTree.SubElement(provides, "view").attrib['uri'] = \
                CADC_VO_VIEWS['data']

        # if this is a container node then add directory contents
        if node_type == "vos:ContainerNode":
            node_list = ElementTree.SubElement(node, Node.NODES)
            for sub_node in subnodes:
                node_list.append(sub_node.node)

        return node

    def isdir(self):
        """Check if target is a container Node"""
        # logger.debug(self.type)
        if self.type == "vos:ContainerNode":
            return True
        return False

    def islink(self):
        """Check if target is a link Node"""
        # logger.debug(self.type)
        if self.type == "vos:LinkNode":
            return True
        return False

    @property
    def is_locked(self):
        return self.islocked()

    @is_locked.setter
    def is_locked(self, lock):
        if lock == self.is_locked:
            return
        self.change_prop(VO_PROPERTY_URI_ISLOCKED, lock and "true" or None)

    def islocked(self):
        """Check if target state is locked for update/delete."""
        return self.props.get(VO_PROPERTY_URI_ISLOCKED) == "true"

    def get_info(self):
        """Organize some information about a node and return as dictionary"""
        date = convert_vospace_time_to_seconds(self.props['date'])
        creator_str = re.search('CN=([^,]*)',
                                self.props.get('creator', 'CN=unknown_000,'))
        if creator_str is None:
            creator = self.props.get('creator', 'CN=unknown_000,')
        else:
            creator = (creator_str.groups()[0].replace(' ', '_')).lower()
        perm = []
        for i in range(10):
            perm.append('-')
        perm[1] = 'r'
        perm[2] = 'w'
        if self.type == "vos:ContainerNode":
            perm[0] = 'd'
        if self.type == "vos:LinkNode":
            perm[0] = 'l'
        if self.props.get('ispublic', "false") == "true":
            perm[-3] = 'r'
            perm[-2] = '-'
        write_group = self.props.get('groupwrite', 'NONE')
        if write_group != 'NONE':
            perm[5] = 'w'
        read_group = self.props.get('groupread', 'NONE')
        if read_group != 'NONE':
            perm[4] = 'r'
        is_locked = self.props.get(VO_PROPERTY_URI_ISLOCKED, "false")
        return {"permissions": ''.join(perm),
                "creator": creator,
                "readGroup": read_group,
                "writeGroup": write_group,
                "isLocked": is_locked,
                "size": float(self.props.get('length', 0)),
                "date": date,
                "target": self.target}

    @property
    def node_list(self):
        """Get a list of all the nodes held to by a ContainerNode return a
           list of Node objects

        :rtype: list
        """
        if self._node_list is None:
            self._node_list = []
            for nodesNode in self.node.findall(Node.NODES):
                for nodeNode in nodesNode.findall(Node.NODE):
                    self.add_child(nodeNode)
        return self._node_list

    def get_children(self, client, sort, order, limit=None):
        """ Gets an iterator over the nodes held to by a ContainerNode"""
        # IF THE CALLER KNOWS THEY DON'T NEED THE CHILDREN THEY
        # CAN SET LIMIT=0 IN THE CALL Also, if the number of nodes
        # on the first call was less than 500, we likely got them
        # all during the init
        if not self.isdir():
            return

        if self.node_list is not None:
            # children already downloaded
            for i in self.node_list:
                yield i

        # stream children
        xml_file = StringIO(
            client.open(self.uri, os.O_RDONLY,
                        limit=limit, sort=sort,
                        order=order).read().decode('UTF-8'))
        xml_file.seek(0)
        page = Node(ElementTree.parse(xml_file).getroot())
        nl = page.node_list
        current_index = 0
        run = True
        while run and nl:
            yield_node = nl[current_index]
            current_index = current_index + 1
            if current_index == len(nl):
                if len(nl) == limit:
                    # do another page read
                    xml_file = StringIO(
                        client.open(self.uri, os.O_RDONLY, next_uri=nl[-1].uri,
                                    sort=sort, order=order,
                                    limit=limit).read().decode('UTF-8'))
                    xml_file.seek(0)
                    page = Node(ElementTree.parse(xml_file).getroot())
                    nl = page.node_list
                    if len(nl) == 1 and nl[0].uri == yield_node.uri:
                        # that must be the last node
                        run = False
                    else:
                        # skip first returned entry as it is the same with
                        # the last one from the previous batch
                        current_index = 1
                else:
                    run = False
            with nodeCache.watch(yield_node.uri) as childWatch:
                childWatch.insert(yield_node)
            yield yield_node

    def add_child(self, child_element_tree):
        """
        Add a child node to a node list.
        :param child_element_tree: a node to add as a child.
        :type child_element_tree: ElementTree
        :return: Node
        """
        child_node = Node(child_element_tree)
        self.node_list.append(child_node)
        return child_node

    def clear_properties(self):
        logger.debug("clearing properties")
        properties_node_list = self.node.findall(Node.PROPERTIES)
        for properties_node in properties_node_list:
            for property_node in properties_node.findall(Node.PROPERTY):
                key = self.get_prop_name(property_node.get('uri'))
                if key in self.props:
                    del self.props[key]
                properties_node.remove(property_node)
        logger.debug("done clearing properties")
        return

    def get_info_list(self):
        """
        :rtype [(Node, dict)]
        :return a list of tuples containing the (NodeName, Info) about the
        node and its childern
        """
        info = {}
        for node in self.node_list:
            info[node.name] = node.get_info()
        if self.type == "vos:DataNode":
            info[self.name] = self.get_info()
        return info.items()

    def set_props(self, props):
        """Set the SubElement Node PROPERTY values of the given xmlx ELEMENT
        provided using the Nodes props dictionary.

        :param props: the xmlx element to set the Node PROPERTY of.
        """
        for property_node in props.findall(Node.PROPERTY):
            self.props[self.get_prop_name(
                property_node.get('uri'))] = self.get_prop_value(property_node)
        return

    @staticmethod
    def get_prop_name(prop):
        """parse the property uri and get the name of the property (strips off
        the url and just returns the tag)
        if this is an IVOA property, otherwise sends back the entry uri.

        :param prop: the uri of the property to get the name of.

        """
        parts = urlparse(prop)
        if '{}://{}{}'.format(parts.scheme, parts.netloc, parts.path) == \
                Node.IVOAURL:
            return parts.fragment
        return prop

    @staticmethod
    def get_prop_value(prop):
        """Pull out the value part of PROPERTY Element.

        :param prop: an XML Element that represents a Node PROPERTY.
        """
        return prop.text


class VOFile(object):
    """
    A class for managing http connections

    Attributes:
    maxRetries - maximum number of retries when transient errors encountered.
                 When set too high (as the default value is) the number of
                 retries are time limited (max 15min)
    maxRetryTime - maximum time to retry for when transient errors are
                   encountered
    """
    errnos = {404: errno.ENOENT,
              401: errno.EACCES,
              409: errno.EEXIST,
              423: errno.EPERM,
              408: errno.EAGAIN}
    # ## if we get one of these codes, retry the command... ;-(
    retryCodes = (503, 408, 504, 412)

    def __init__(self, url_list, connector, method, size=None,
                 follow_redirect=True, byte_range=None,
                 possible_partial_read=False):
        self.closed = True
        if not isinstance(connector, Connection):
            raise AttributeError("BUG: Connection expected")
        self.connector = connector
        self.httpCon = None
        self.timeout = -1
        self.size = size
        self.md5sum = None
        self.totalFileSize = None
        self.maxRetries = 10000
        self.maxRetryTime = MAX_RETRY_TIME
        self.url = None
        self.method = None

        # TODO
        # Make all the calls to open send a list of URLs
        # this should be redone during a cleanup. Basically, a GET might
        # result in multiple URLs (list of URLs) but VOFile is also used to
        # retrieve schema files and other info.

        # All the calls should pass a list of URLs. Make sure that we
        # make a deep copy of the input list so that we don't
        # accidentally modify the caller's copy.
        if isinstance(url_list, list):
            self.URLs = deepcopy(url_list)
        else:
            self.URLs = [url_list]
        self.urlIndex = 0
        self.followRedirect = follow_redirect
        self._fpos = 0
        # initial values for retry parameters
        self.currentRetryDelay = DEFAULT_RETRY_DELAY
        self.totalRetryDelay = 0
        self.retries = 0
        self.fileSize = None
        self.request = None
        self.resp = None
        self.trans_encode = None
        # open the connection
        self._fobj = None
        self.open(self.URLs[self.urlIndex], method, byte_range=byte_range,
                  possible_partial_read=possible_partial_read)

    def tell(self):
        return self._fpos

    def seek(self, offset, loc=os.SEEK_SET):
        if loc == os.SEEK_CUR:
            self._fpos += offset
        elif loc == os.SEEK_SET:
            self._fpos = offset
        elif loc == os.SEEK_END:
            self._fpos = int(self.size) - offset
        return

    @staticmethod
    def flush():
        """
        Flush is a NO OP in VOFile: only really flush on close.
        @return:
        """
        return

    def close(self):
        """close the connection."""

        if not self.closed:
            try:
                if self.trans_encode is not None:
                    self.httpCon.send('0\r\n\r\n')
                    logger.debug("End of document sent.")
                logger.debug("getting response.")
                self.resp = self.connector.session.send(self.request)
                logger.debug("checking response status.")
                self.checkstatus()
            finally:
                self.closed = True
        return self.closed

    def checkstatus(self, codes=(200, 201, 202, 206, 302, 303, 503, 404, 416,
                                 416, 402, 408, 412, 504)):
        """check the response status.  If the status code doesn't match a
        value from the codes list then
        raise an Exception.

        :param codes: a list of http status_codes that are NOT failures but
        require some additional action.
        """
        if self.resp is None:
            return

        msgs = {404: "Node Not Found",
                401: "Not Authorized",
                409: "Conflict",
                423: "Locked",
                408: "Connection Timeout"}
        logger.debug(
            'status {} for URL {}'.format(self.resp.status_code, self.url))
        if self.resp.status_code not in codes:
            logger.debug("Got status code: %s for %s" %
                         (self.resp.status_code, self.url))
            msg = self.resp.text
            if msg is not None:
                msg = html2text.html2text(msg, self.url).strip().replace('\n',
                                                                         ' ')
            logger.debug("Error message: {0}".format(msg))

            if self.resp.status_code in VOFile.errnos.keys() or (
                    msg is not None and "Node is busy" in msg):
                if msg is None or len(
                        msg) == 0 and self.resp.status_code in msgs:
                    msg = msgs[self.resp.status_code]
                if (self.resp.status_code == 401 and
                        self.connector.subject.anon and
                        self.connector.vo_token is None):
                    msg += " using anonymous access "
            exception = OSError(VOFile.errnos.get(self.resp.status_code,
                                                  self.resp.status_code), msg)
            if self.resp.status_code == 500 and "read-only" in msg:
                exception = OSError(errno.EPERM, "VOSpace in read-only mode.")
            if self.resp.status_code == 400 and \
                    "sorting options not supported" in msg:
                exception = Exception("service does not support sorting")
            raise exception

        # Get the file size. We use this HEADER-CONTENT-LENGTH as a
        # fallback to work around a server-side Java bug that limits
        # 'Content-Length' to a signed 32-bit integer (~2 gig files)
        try:
            self.size = int(self.resp.headers.get("Content-Length",
                                                  self.resp.headers.get(
                                                      HEADER_CONTENT_LENGTH,
                                                      0)))
        except ValueError:
            self.size = 0

        if self.resp.status_code == 200:
            self.md5sum = self.resp.headers.get("Content-MD5", None)
            self.totalFileSize = self.size

        return True

    def open(self, url, method="GET", byte_range=None,
             possible_partial_read=False):
        """Open a connection to the given URL
        :param url: The URL to be opened
        :type url: unicode
        :param method: HTTP Method to use on open (PUT/GET/POST)
        :type method: unicode
        :param byte_range: The range of byte_range to read, This is in open so
        we can set the header parameter.
        :type byte_range: unicode
        :param possible_partial_read:  Sometimes we kill during read, this
        tells the server that isn't an error.
        :type possible_partial_read: bool
        """
        logger.debug('Opening {} ({})'.format(url, method))
        self.url = url
        self.method = method

        request = requests.Request(self.method, url)

        self.trans_encode = None

        # Try to send a content length hint if this is a PUT.
        # otherwise send as a chunked PUT
        if method in ["PUT"]:
            try:
                self.size = int(self.size)
                request.headers.update({"Content-Length": str(self.size),
                                        HEADER_CONTENT_LENGTH: str(self.size)})
            except TypeError:
                self.size = None
                self.trans_encode = "chunked"
        elif method in ["POST", "DELETE"]:
            self.size = None
            self.trans_encode = "chunked"

        if method in ["PUT", "POST", "DELETE"]:
            content_type = "text/xml"
            if method == "PUT":
                ext = os.path.splitext(urllib.splitquery(url)[0])[1]
                if ext in ['.fz', '.fits', 'fit']:
                    content_type = 'application/fits'
                else:
                    content_type = mimetypes.guess_type(url)[0]
            if content_type is not None:
                request.headers.update({"Content-Type": content_type})
        if byte_range is not None and method == "GET":
            request.headers.update({"Range": byte_range})
        request.headers.update({"Accept": "*/*",
                                "Expect": "100-continue"})

        # set header if a partial read is possible
        if possible_partial_read and method == "GET":
            request.headers.update({HEADER_PARTIAL_READ: "true"})
        try:
            self.request = self.connector.session.prepare_request(request)
        except Exception as ex:
            logger.error(str(ex))

    def get_file_info(self):
        """Return information harvested from the HTTP header.

        :rtype (int, unicode)
        """
        return self.totalFileSize, self.md5sum

    def read(self, size=None, return_response=False):
        """return size bytes from the connection response

        :param return_response: should we return the response object or the
        bytes read?
        :param size: number of bytes to read from the file.
        """

        if self.resp is None:
            # this is original retry flag of the session
            orig_retry_flag = self.connector.session.retry
            try:
                if (len(self.URLs) > 1) and\
                   (self.urlIndex < len(self.URLs) - 1):
                    # there is more urls to try so don't bother retrying on
                    # transient errors
                    # return instead and try the next url
                    self.connector.session.retry = False
                self.resp = self.connector.session.send(self.request,
                                                        stream=True)
            except exceptions.HttpException as http_exception:
                if 'SSLV3_ALERT_CERTIFICATE_EXPIRED' in str(http_exception):
                    raise RuntimeError(
                        'Expired cert. Update by running cadc-get-cert')
                # this is the path for all status_codes between 400 and 600
                if http_exception.orig_exception is not None:
                    self.resp = http_exception.orig_exception.response

                # restore the original retry flag of the session
                self.connector.session.retry = orig_retry_flag

                self.checkstatus()

                if isinstance(http_exception,
                              exceptions.UnauthorizedException) or \
                        isinstance(http_exception,
                                   exceptions.BadRequestException) or \
                        isinstance(http_exception,
                                   exceptions.ForbiddenException):
                    raise

                # Note: 404 (File Not Found) might be returned when:
                # 1. file deleted or replaced
                # 2. file migrated from cache
                # 3. hardware failure on storage node
                # For 3. it is necessary to try the other URLs in the list
                #   otherwise this the failed URL might show up even after the
                #   caller tries to re-negotiate the transfer.
                # For 1. and 2., calls to the other URLs in the list might or
                #   might not succeed.
                if self.urlIndex < len(self.URLs) - 1:
                    # go to the next URL
                    self.urlIndex += 1
                    self.open(self.URLs[self.urlIndex], "GET")
                    self.resp = None
                    return self.read(size)
                else:
                    raise
            finally:
                # restore the original retry flag of the session
                self.connector.session.retry = orig_retry_flag

        # Get the file size. We use this HEADER-CONTENT-LENGTH as a
        # fallback to work around a server-side Java bug that limits
        # 'Content-Length' to a signed 32-bit integer (~2 gig files)
        try:
            self.size = int(self.resp.headers.get("Content-Length",
                                                  self.resp.headers.get(
                                                      HEADER_CONTENT_LENGTH,
                                                      0)))
        except Exception:
            self.size = 0

        if self.resp.status_code == 200:
            self.md5sum = self.resp.headers.get("Content-MD5", None)
            self.totalFileSize = self.size

        if self.resp is None:
            raise OSError(errno.EFAULT, "No response from VOServer")

        # check the most likely response first
        if self.resp.status_code == 200 or self.resp.status_code == 206:
            if return_response:
                return self.resp
            else:
                buff = self.resp.raw.read(size)
                size = size is not None and size < len(buff) and size or len(
                    buff)
                # logger.debug("Sending back {0} bytes".format(size))
                return buff[:size]
        elif self.resp.status_code == 303 or self.resp.status_code == 302:
            url = self.resp.headers.get('Location', None)
            logger.debug("Got redirect URL: {0}".format(url))
            self.url = url
            if not url:
                raise OSError(
                    errno.ENOENT,
                    "Got 303 on {0} but no Location value in header? [{1}]".
                    format(self.url, self.resp.content),
                    self.url)
            if self.followRedirect:
                # We open this new URL without the byte range and partial
                # read as we are following a service redirect and that service
                # redirect is to the object that satisfies the original
                # request.
                # TODO seperate out making the transfer reqest and reading
                # the response content.
                self.open(url, "GET")
                return self.read(size)
            else:
                return self.url

        # start from top of URLs with a delay
        self.urlIndex = 0
        logger.error("Servers busy {0} for {1}".format(self.resp.status_code,
                                                       self.URLs))
        msg = self.resp.text
        if msg is not None:
            msg = html2text.html2text(msg, self.url).strip()
        else:
            msg = "No Message Sent"
        logger.error("Message from VOSpace {0}: {1}".format(self.url, msg))
        try:
            # see if there is a Retry-After in the head...
            ras = int(self.resp.headers.get("Retry-After", 5))
        except ValueError:
            ras = self.currentRetryDelay
            if (self.currentRetryDelay * 2) < MAX_RETRY_DELAY:
                self.currentRetryDelay *= 2
            else:
                self.currentRetryDelay = MAX_RETRY_DELAY

        if ((self.retries < self.maxRetries) and
                (self.totalRetryDelay < self.maxRetryTime)):
            logger.error("Retrying in {0} seconds".format(ras))
            self.totalRetryDelay += ras
            self.retries += 1
            time.sleep(int(ras))
            self.open(self.URLs[self.urlIndex], "GET")
            self.resp = None
            return self.read(size)
        else:
            raise OSError(
                self.resp.status_code,
                "failed to connect to server after multiple attempts {0} {1}".
                format(self.resp.reason, self.resp.status_code),
                self.url)

    @staticmethod
    def write(buf):
        """write buffer to the connection

        :param buf: string to write to the file.
        """
        raise OSError(
            errno.ENOSYS,
            "Direct write to a VOSpaceFile is not supported, use "
            "copy instead.")


class EndPoints(object):
    VOSPACE_WEBSERVICE = os.getenv('VOSPACE_WEBSERVICE', os.getenv('LOCAL_VOSPACE_WEBSERVICE', None))

    # standard ids
    VO_NODES = 'ivo://ivoa.net/std/VOSpace/v2.0#nodes'
    VO_FILES = 'ivo://ivoa.net/std/VOSpace#files-proto'
    VO_TRANSFER = 'ivo://ivoa.net/std/VOSpace#sync-2.1'
    VO_ASYNC_TRANSFER = 'ivo://ivoa.net/std/VOSpace/v2.0#transfers'
    VO_RECURSIVE_DEL = 'ivo://ivoa.net/std/VOSpace#recursive-delete-proto'
    VO_RECURSIVE_PROPS = 'ivo://ivoa.net/std/VOSpace#recursive-nodeprops-proto'

    subject = net.Subject()  # default subject is for anonymous access

    def __init__(self, resource_id_uri, vospace_certfile=None,
                 vospace_token=None, insecure=False):
        """
        Determines the end points of a vospace service
        :param resource_id_uri: the resource id uri
        :type resource_id_uri: unicode
        :param vospace_certfile: x509 proxy certificate file location.
        Overrides certfile in conn.
        :type vospace_certfile: unicode
        :param vospace_token: token string (alternative to vospace_certfile)
        :type vospace_token: unicode
        :param insecure: Allow insecure server connections when using SSL
        :type insecure: bool
        """
        self.resource_id = resource_id_uri
        self.conn = Connection(vospace_certfile=vospace_certfile,
                               vospace_token=vospace_token,
                               resource_id=self.resource_id,
                               insecure=insecure)

    @property
    def uri(self):
        return self.resource_id

    @property
    def server(self):
        """
        Returns the server where the __nodes__ capability is deployed. Most
        of the time all the capabilities are deployed on the same server but
        sometimes they might not be.
        :return: The network location of the VOSpace server.
        """
        return urlparse(self.nodes).netloc

    @property
    def transfer(self):
        """
        The sync transfer service endpoint.
        :return: service location of the transfer service.
        :rtype: unicode
        """
        return self.conn.ws_client._get_url((self.VO_TRANSFER, None))

    @property
    def async_transfer(self):
        """
        The async transfer service endpoint
        :return: location of the async transfer service
        :return:
        """
        return self.conn.ws_client._get_url((self.VO_ASYNC_TRANSFER, None))

    @property
    def nodes(self):
        """
        :return: The Node service endpoint.
        """
        return self.conn.ws_client._get_url((self.VO_NODES, None))

    @property
    def files(self):
        """
        :return: The files service endpoint.
        """
        return self.conn.ws_client._get_url((self.VO_FILES, None))

    @property
    def recursive_del(self):
        """
        :return: recursive delete endpoint
        """
        return self.conn.ws_client._get_url((self.VO_RECURSIVE_DEL, None))

    @property
    def recursive_props(self):
        """
        :return: recusive property set endpoint
        """
        return self.conn.ws_client._get_url((self.VO_RECURSIVE_PROPS, None))

    @property
    def session(self):
        # TODO can we use just the ws_client instead?
        return self.conn.ws_client._get_session()

    def set_auth(self, vospace_certfile=None, vospace_token=None):
        """
        Resets the authentication to be used with this service
        :param vospace_certfile: x509 proxy certificate file location.
        Overrides certfile in conn.
        :type vospace_certfile: unicode
        :param vospace_token: token string (alternative to vospace_certfile)
        :type vospace_token: unicode
        """
        self.conn = Connection(vospace_certfile=vospace_certfile,
                               vospace_token=vospace_token,
                               resource_id=self.resource_id)


nodeCache = NodeCache()


class Client(object):
    """The Client object does the work"""

    VO_HTTPGET_PROTOCOL = 'ivo://ivoa.net/vospace/core#httpget'
    VO_HTTPPUT_PROTOCOL = 'ivo://ivoa.net/vospace/core#httpput'
    VO_HTTPSGET_PROTOCOL = 'ivo://ivoa.net/vospace/core#httpsget'
    VO_HTTPSPUT_PROTOCOL = 'ivo://ivoa.net/vospace/core#httpsput'
    DWS = '/data/pub/'
    VO_TRANSFER_PROTOCOLS = ['https', 'http']

    #  reserved vospace properties, not to be used for extended property
    #  setting
    vosProperties = ["description", "type", "encoding", "MD5", "length",
                     "creator", "date", "groupread", "groupwrite", "ispublic"]

    VOSPACE_CERTFILE = os.getenv("VOSPACE_CERTFILE", None)
    if VOSPACE_CERTFILE is None:
        for certfile in ['cadcproxy.pem', 'vospaceproxy.pem']:
            certpath = os.path.join(os.getenv("HOME", "."), '.ssl')
            certfilepath = os.path.join(certpath, certfile)
            if os.access(certfilepath, os.R_OK):
                VOSPACE_CERTFILE = certfilepath
            break

    def __init__(self, vospace_certfile=None,
                 root_node=None, conn=None,
                 transfer_shortcut=None, http_debug=False,
                 secure_get=True, vospace_token=None, insecure=False):
        """This could/should be expanded to set various defaults
        :param vospace_certfile: x509 proxy certificate file location. The
        certificate will be used with all the services that the Client
        communicates to. To set auth for individual services use `set_auth`
        method.
        :type vospace_certfile: unicode
        :param vospace_token: token string (alternative to vospace_certfile)
        The token will be used with all the services that the Client
        communicates to. To set auth for individual services use `set_auth`
        method.
        :type vospace_token: unicode
        :param root_node: the base of the VOSpace for uri references.
        :type root_node: unicode
        :param conn: DEPRECATED
        :param transfer_shortcut: DEPRECATED
        :param http_debug: turn on http debugging.
        :type http_debug: bool
        :param secure_get: Use HTTPS (by default): ie. transfer contents of
        files using SSL encryption. Used for more and more unlikely case when
        the service supports unsecure (HTTP) transfer.
        :type secure_get: bool
        :param insecure: Allow insecure server connections when using SSL
        :type insecure: bool
        :
        """

        util.check_version(version=version)

        if os.getenv('VOSPACE_WEBSERVICE', None):
            msg = 'Using custom host: env.VOSPACE_WEBSERVICE={}'.\
                  format(os.getenv('VOSPACE_WEBSERVICE', None))
            logging.getLogger().warning(msg)
        elif os.getenv('LOCAL_VOSPACE_WEBSERVICE', None):
            msg = 'Using custom host: env.LOCAL_VOSPACE_WEBSERVICE={}'.\
                  format(os.getenv('LOCAL_VOSPACE_WEBSERVICE', None))
            logging.getLogger().warning(msg)

        protocol = vos_config.get('transfer', 'protocol')
        if protocol is not None:
            warn_msg = "Protocol is no longer supported and should be " \
                       "removed from the config file."
            warnings.warn(warn_msg, UserWarning)

        if conn is not None:
            warn_msg = "conn argument no longer used in vos.Client ctor."
            warnings.warn(warn_msg, UserWarning)

        self.protocols = Client.VO_TRANSFER_PROTOCOLS
        self.rootNode = root_node
        # self.nodeCache = NodeCache()
        self.secure_get = secure_get
        self._endpoints = {}
        self.vospace_certfile = vospace_certfile is None and \
            Client.VOSPACE_CERTFILE or vospace_certfile
        self.vospace_token = vospace_token
        self.insecure = insecure
        self._fs_type = True  # True - file system type (cavern), False - db type (vault)
        self._si_client = None

    def glob(self, pathname):
        """Return a list of paths matching a pathname pattern.

        The pattern may contain simple shell-style wildcards a la
        fnmatch. However, unlike fnmatch, file names starting with a
        dot are special cases that are not matched by '*' and '?'
        patterns.

        :param pathname: path to glob.

        """
        return list(self.iglob(pathname))

    def iglob(self, pathname):
        """Return an iterator which yields the paths matching a pathname
        pattern.

        The pattern may contain simple shell-style wildcards a la fnmatch.
        However, unlike fnmatch, filenames
        starting with a dot are special cases that are not matched by '*'
        and '?' patterns.

        :param pathname: path to run glob against.
        :type pathname: unicode
        """
        dirname, basename = os.path.split(pathname)
        if not self.has_magic(pathname):
            if basename:
                self.get_node(pathname)
                yield pathname
            else:
                # Patterns ending with a slash should match only directories
                if self.iglob(dirname):
                    yield pathname
            return
        if not dirname:
            for name in self.glob1(self.rootNode, basename):
                yield name
            return
        # `os.path.split()` returns the argument itself as a dirname if it is a
        # drive or UNC path.  Prevent an infinite recursion if a drive or UNC
        # path contains magic characters (i.e. r'\\?\C:').
        if dirname != pathname and self.has_magic(dirname):
            dirs = self.iglob(dirname)
        else:
            dirs = [dirname]
        if self.has_magic(basename):
            glob_in_dir = self.glob1
        else:
            glob_in_dir = self.glob0
        for dirname in dirs:
            for name in glob_in_dir(dirname, basename):
                yield os.path.join(dirname, name)

    # These 2 helper functions non-recursively glob inside a literal directory.
    # They return a list of basenames. `glob1` accepts a pattern while `glob0`
    # takes a literal basename (so it only has to check for its existence).

    def glob1(self, dirname, pattern):
        """

        :param dirname: name of the directory to look for matches in.
        :type dirname: unicode
        :param pattern: pattern to match directory contents names against
        :type pattern: unicode
        :return:
        """
        if not dirname:
            dirname = self.rootNode
        if isinstance(pattern, str) and not isinstance(dirname, str):
            dirname = str(dirname).encode(
                sys.getfilesystemencoding() or sys.getdefaultencoding())
        try:
            names = self.listdir(dirname, force=True)
        except os.error:
            return []
        if not pattern.startswith('.'):
            names = filter(lambda x: not x.startswith('.'), names)
        return fnmatch.filter(names, pattern)

    def glob0(self, dirname, basename):
        if basename == '':
            # `os.path.split()` returns an empty basename for paths ending
            # with a directory separator.  'q*x/' should match only
            # directories.
            if self.isdir(dirname):
                return [basename]
        else:
            if self.access(os.path.join(dirname, basename)):
                return [basename]
            else:
                raise OSError(errno.EACCES, "Permission denied: {0}".format(
                    os.path.join(dirname, basename)))
        return []

    def set_auth(self, uri, vospace_certfile=None, vospace_token=None):
        """
        Sets a certificate to be used with a specific service.
        :param uri - the uri of the service (scheme ivo) or an uri to a
        resource on that service (scheme vos or configured resource name)
        :param vospace_certfile: x509 proxy certificate file location.
        Overrides certfile in conn.
        :type vospace_certfile: unicode
        :param vospace_token: token string (alternative to vospace_certfile)
        :type vospace_token: unicode
        """
        self.get_endpoints(uri).set_auth(vospace_certfile=vospace_certfile,
                                         vospace_token=vospace_token)

    def is_remote_file(self, file_name):
        if file_name.startswith(('http://', 'https://')):
            # assume full uri
            return True
        file_scheme = urlparse(file_name).scheme
        if file_scheme:
            try:
                self.get_endpoints(file_name)
                return True
            except Exception as ex:
                msg = 'No VOSpace service found for {}'.format(file_name)
                logger.debug('{}, Reason: {}'.format(msg, ex))
                raise ValueError(msg)
        return False

    def get_endpoints(self, uri):
        """
        Returns the end points or a vospace service corresponding to an uri

        The main purpose of this method is to cache the EndPoints for used
        services for performance reasons.

        :param uri: uri of and entry or of a resource id for which the end
        points are seek
        :return: corresponding EndPoint object
        """

        uri_parts = urlparse(uri)
        if uri.startswith('ivo://'):
            raise AttributeError(
                'BUG: VOSpace identifier expected (vos scheme), '
                'received registry identifier {}'.format(uri))
        if uri.startswith('vos://'):
            resource_id = 'ivo://{}'.format(
                uri_parts.hostname.replace('!', '/').replace('~', '/'))
        else:
            if uri_parts.scheme is not None:
                # assume first that the file_scheme is the short name of the
                # resource e.g. arc corresponds to ivo://cadc.nrc.ca/arc
                # With a proper reg, this could be replaced by a TAP search
                # into the registry
                if uri_parts.scheme == 'vos':
                    # special shortcut
                    scheme = 'vault'
                else:
                    scheme = uri_parts.scheme
                if (os.getenv('LOCAL_VOSPACE_WEBSERVICE')):
                    # assume testing against local deployment
                    resource_id = 'ivo://opencadc.org/{}'.format(scheme)
                else:
                    resource_id = 'ivo://cadc.nrc.ca/{}'.format(scheme)

            else:
                raise OSError('No scheme in {}'.format(uri))
        # following is a CADC hack as others can deploy the services under different
        # resource IDs
        if 'vault' in resource_id:
            self._fs_type = False
        if resource_id not in self._endpoints:
            try:
                self._endpoints[resource_id] = EndPoints(
                    resource_id, vospace_certfile=self.vospace_certfile,
                    vospace_token=self.vospace_token, insecure=self.insecure)
            except Exception:
                # no services by that short name. Try a shortcut from
                # config (only for backwards compatibility)
                try:
                    resource_id = vos_config.get_resource_id(scheme)
                    self._endpoints[resource_id] = EndPoints(
                        resource_id, vospace_certfile=self.vospace_certfile,
                        vospace_token=self.vospace_token,
                        insecure=self.insecure)
                except Exception:
                    raise AttributeError(
                        'No service with resource ID {} found in registry or '
                        'the config file'.format(resource_id))
        return self._endpoints[resource_id]

    def get_session(self, uri):
        return self.get_endpoints(uri).session

    @staticmethod
    def has_magic(s):
        return MAGIC_GLOB_CHECK.search(s) is not None

    def _get_si_client(self, uri):
        ep = self.get_endpoints(uri)
        if not self._si_client:
            self._si_client = net.BaseDataClient(ep.resource_id, ep.subject,
                                                 ep.conn.ws_client.agent, retry=True,
                                                 host=ep.conn.ws_client.host,
                                                 insecure=self.insecure,
                                                 server_versions=SUPPORTED_SERVER_VERSIONS)
        return self._si_client

    # @logExceptions()
    def copy(self, source, destination, send_md5=False, disposition=False,
             head=None):
        """copy from source to destination.

        One of source or destination must be a vospace location and the other
        must be a local location.

        :param source: The source file to send to VOSpace or the VOSpace node
        to retrieve
        :type source: unicode
        :param destination: The VOSpace location to put the file to or the
        local destination.
        :type destination: Node
        :param send_md5: Should copy send back the md5 of the destination
        file or just the size?
        :type send_md5: bool
        :param disposition: Should the filename from content disposition be
        returned instead of size or MD5?
        :type disposition: bool
        :param head: Return just the headers of a file.
        :type head: bool
        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module

        """
        # TODO: handle vospace to vospace copies.

        success = False
        copy_failed_message = ""
        dest_size = None
        src_md5 = None
        must_delete = False  # delete node after failed transfer
        transf_file = None
        get_node_url_retried = False
        # url retry counter - how many times an url has been retried

        if self.is_remote_file(source):
            # GET
            retried_urls = {}
            files_url = None
            if destination is None:
                # Set the destination, initially, to the same directory as
                # the source (strip the scheme)
                destination = os.path.dirname(urlparse(source).path)
            if os.path.isdir(destination):
                # We can't write to a directory so take file name from
                # content-disposition or from filename part of source.
                disposition = True
            get_urls = []
            cutout_match = FILENAME_PATTERN_MAGIC.search(source)
            if cutout_match is not None and cutout_match.group('cutout'):
                view = 'cutout'
                if cutout_match.group('pix'):
                    cutout = cutout_match.group('pix')
                elif cutout_match.group('wcs') is not None:
                    cutout = 'CIRCLE=' + '{} {} {}'.format(
                        cutout_match.group('ra'),
                        cutout_match.group('dec'),
                        cutout_match.group('rad'))
                else:
                    raise ValueError("Bad source name: {}".format(source))
                source = cutout_match.group('filename')
            elif head:
                view = 'header'
                cutout = None
            else:
                view = 'data'
                cutout = None

            if self._fs_type and (cutout or view == 'header'):
                raise ValueError('cavern/arc service does not support cutouts or header operations')

            files_url = self.get_node_url(source, method='GET',
                                          cutout=cutout,
                                          view=view)
            if isinstance(files_url, list) and len(files_url) > 0:
                files_url = files_url.pop(0)
            try:
                transf_file = self._get_si_client(source).download_file(
                    url=files_url, dest=destination,
                    params=self._get_soda_params(view=view, cutout=cutout))
                success = True
            except Exception as e:
                # not much to do but to fall through with full negotiation
                logger.debug('GET fail on files endpoint for {}'.format(source), e)

            if not success:
                # at this point it's probably time to check whether the node is actually empty
                src_node = self.get_node(source, force=True)
                src_size = src_node.props.get('length', None)
                src_size = int(src_size)
                if src_size == 0:
                    dest_file = destination
                    if os.path.isdir(dest_file):
                        dest_file = os.path.join(dest_file, os.path.basename(source))
                    open(dest_file, 'wb').write(b'')  # empty file
                    transf_file = dest_file, ZERO_MD5, 0
                    dest_size = 0
                    success = True

            while not success:
                if len(get_urls) == 0:
                    if not get_node_url_retried:
                        get_urls = self.get_node_url(source, method='GET',
                                                     cutout=cutout, view=view,
                                                     full_negotiation=True)
                        if len(get_urls) > 1:
                            # remove files_url that we've tried already
                            get_urls = [url for url in get_urls if url != files_url]
                        get_node_url_retried = True
                if len(get_urls) == 0:
                    break
                get_url = get_urls.pop(0)

                try:
                    transf_file = self._get_si_client(source).download_file(
                        url=get_url, dest=destination,
                        params=self._get_soda_params(view=view, cutout=cutout))
                    success = True
                    break
                except exceptions.HttpException as ex:
                    msg = ''
                    if isinstance(ex, exceptions.TransferException):
                        msg = ' (intermittent error)'
                        retried_urls[get_url] = \
                            retried_urls.get(get_url, 0) + 1
                        if retried_urls[get_url] < MAX_INTERMTTENT_RETRIES:
                            # intermittent error - worth retrying url later
                            get_urls.append(get_url)
                    copy_failed_message = str(ex)
                    logging.debug("Failed to GET {0}: {1}{2}".format(
                        get_url, str(ex), msg))
                    continue
        else:
            # PUT
            success = False
            dest_size = None
            destination_node = None
            dest_node_md5 = None
            try:
                destination_node = self.get_node(destination, force=True)
                dest_node_md5 = destination_node.props.get('MD5', None)
                dest_size = destination_node.props.get('length', None)
                if dest_size:
                    dest_size = int(dest_size)
            except Exception:
                pass
            src_size = os.stat(source).st_size
            # check 2 cases where file sending not required:
            #   1. source file is empty -> just re-create the node
            #   2. source and destination are identical
            if src_size == 0:
                logger.info("src: size is 0")
                if destination_node:
                    # TODO delete and recreate the node. Is there a better way
                    # to delete just the content of the node?
                    self.delete(destination)
                self.create(destination)
                transf_file = os.path.basename(destination), ZERO_MD5, 0
                success = True
            elif src_size == dest_size:
                if dest_node_md5 is not None:
                    # compute the md5 of the source file. This serves 2
                    # purposes:
                    #   1. Check if source is identical to destination and
                    #   avoid sending the bytes again.
                    #   2. send info to the service so that it can recover in case
                    #   the bytes got corrupted on the way
                    src_md5 = md5_cache.MD5Cache.compute_md5(source)
                    if src_md5 == dest_node_md5:
                        logger.info('Source and destination identical for {}. Skip transfer!'.format(source))
                        # post the node so that the modify time is updated
                        self.update(destination_node)
                        transf_file = os.path.basename(destination), dest_node_md5, dest_size
                        success = True
            if not success:
                # transfer the bytes with source md5 available
                while not success:
                    if not get_node_url_retried:
                        put_urls = self.get_node_url(
                            destination, method='PUT',
                            full_negotiation=True)
                        get_node_url_retried = True
                    if len(put_urls) == 0:
                        break
                    put_url = put_urls.pop(0)
                    try:
                        transf_file = self._get_si_client(destination).upload_file(
                            url=put_url,
                            src=source,
                            md5_checksum=src_md5)
                    except Exception as ex:
                        msg = ''
                        if isinstance(ex, exceptions.TransferException):
                            msg = ' (intermittent error)'
                            retried_urls[put_url] = retried_urls.get(
                                put_url, 0) + 1
                            if retried_urls[put_url] < \
                                    MAX_INTERMTTENT_RETRIES:
                                # intermittent error - worth retrying later
                                put_urls.append(put_url)
                        copy_failed_message = str(ex)
                        logger.debug(
                            "FAILED to PUT to {0}: {1}{2}".format(
                                put_url, str(ex), msg))
                        continue
                    success = True
                    break
        if not success:
            if must_delete:
                # cleanup
                self.delete(destination)
            raise OSError(errno.EFAULT,
                          "Failed copying {0} -> {1}.\nReason for failure: {2}".
                          format(source, destination, copy_failed_message))
        else:
            logger.info('Transfer successful')
        if transf_file is None:
            raise RuntimeError('BUG: Not found details of successful transfer')
        if disposition and transf_file:
            return transf_file[0]  # file name
        if send_md5 and transf_file:
            return transf_file[1]  # file md5
        return transf_file[2]  # file size

    def fix_uri(self, uri):
        """given a uri check if the authority part is there and if it isn't
        then add the VOSpace authority

        :param uri: The string that should be parsed into a proper URI, if
        possible.

        """
        if '://' in uri:
            # no much to do
            return uri
        parts = urlparse(uri)

        if not self.is_remote_file(uri):
            if self.rootNode is not None:
                uri = self.rootNode + uri
            else:
                raise AttributeError(
                    'Invalid URI to the remote resource: {}'.format(uri))
        parts = urlparse(uri)

        # Check that path name compiles with the standard
        logger.debug("Got value of query: {0}".format(parts.query))
        linkuri = parse_qs(parts.query).get('link', None)
        if linkuri:
            logger.debug("Got uri: {0}".format(linkuri[0]))
            if linkuri[0] is not None:
                # TODO This does not work with invalid links. Should it?
                return self.fix_uri(linkuri[0])

        # Check for filename values.
        path = FILENAME_PATTERN_MAGIC.match(os.path.normpath(parts.path))
        if path is None or path.group('filename') is None:
            raise OSError(errno.EINVAL, "Illegal vospace container name",
                          parts.path)
        logger.debug("Match : {}".format(path.groupdict()))

        filename = path.group('filename')

        # insert the default VOSpace server if none given
        host = parts.netloc
        if not host or host == '':
            # default host corresponds to the resource ID of the client
            host = self.get_endpoints(uri).uri.\
                replace('ivo://', '').replace('/', '!')

        path = os.path.normpath(filename).strip('/')
        # accessing root results in path='.' wich is not a valid root path.
        # Therefore, remove the '.' character in this case
        if path == '.':
            path = ''
        uri = "vos://{0}/{1}{2}".format(
            host, path, "?{}".format(parts.query) if parts.query else "")
        logger.debug("Returning URI: {0}".format(uri))
        return uri

    def get_node(self, uri, limit=0, force=False):
        """connect to VOSpace and download the definition of VOSpace node

        :param uri:   -- a voSpace node in the format vos:/VOSpaceName/nodeName
        :type uri: unicode
        :param limit: -- load children nodes in batches of limit
        :type limit: int, None
        :param force: force getting the node from the service, rather than
        returning a cached version.
        :return: The VOSpace Node
        :rtype: Node

        """
        logger.debug("Getting node {0}".format(uri))
        uri = self.fix_uri(uri)
        node = None
        if not force and uri in nodeCache:
            node = nodeCache[uri]
        if node is None:
            logger.debug("Getting node {0} from ws".format(uri))
            with nodeCache.watch(uri) as watch:
                # If this is vospace URI then we can request the node info
                # using the uri directly, but if this a URL then the metadata
                # comes from the HTTP header.
                # TODO removed ad. Not sure it was used
                if self.is_remote_file(uri):
                    vo_fobj = self.open(uri, os.O_RDONLY, limit=limit)
                    vo_xml_string = vo_fobj.read().decode('UTF-8')
                    xml_file = StringIO(vo_xml_string)
                    xml_file.seek(0)
                    dom = ElementTree.parse(xml_file)
                    node = Node(dom.getroot())
                elif uri.startswith('http'):
                    header = self.open(None, url=uri, mode=os.O_RDONLY,
                                       head=True)
                    header.read()
                    logger.debug(
                        "Got http headers: {0}".format(header.resp.headers))
                    properties = {
                        'type': header.resp.headers.get('Content-Type', 'txt'),
                        'date': time.strftime(
                            '%Y-%m-%dT%H:%M:%S GMT',
                            time.strptime(
                                header.resp.headers.get('Date', None),
                                '%a, %d %b %Y %H:%M:%S GMT')),
                        'groupwrite': None,
                        'groupread': None,
                        'ispublic': urlparse(
                            uri).scheme == 'https' and 'true' or 'false',
                        'length': header.resp.headers.get('Content-Length', 0)}
                    node = Node(node=uri, node_type=Node.DATA_NODE,
                                properties=properties)
                    logger.debug(str(node))
                else:
                    raise OSError(2, "Bad URI {0}".format(uri))
                watch.insert(node)
                # IF THE CALLER KNOWS THEY DON'T NEED THE CHILDREN THEY
                # CAN SET LIMIT=0 IN THE CALL Also, if the number of nodes
                # on the firt call was less than 500, we likely got them
                # all during the init
                if limit != 0 and node.isdir() and len(node.node_list) > 500:
                    next_uri = None
                    while next_uri != node.node_list[-1].uri:
                        next_uri = node.node_list[-1].uri
                        xml_file = StringIO(
                            self.open(uri, os.O_RDONLY, next_uri=next_uri,
                                      limit=limit).read().decode('UTF-8'))
                        xml_file.seek(0)
                        next_page = Node(ElementTree.parse(xml_file).getroot())
                        if len(next_page.node_list) > 0 and next_uri == \
                                next_page.node_list[0].uri:
                            next_page.node_list.pop(0)
                        node.node_list.extend(next_page.node_list)
        for childNode in node.node_list:
            with nodeCache.watch(childNode.uri) as childWatch:
                childWatch.insert(childNode)
        return node

    def get_node_url(self, uri, method='GET', view=None, limit=None,
                     next_uri=None, cutout=None, sort=None, order=None,
                     full_negotiation=None, content_length=None,
                     md5_checksum=None):
        """Split apart the node string into parts and return the correct URL
        for this node.

        :param uri: The VOSpace uri to get an associated url for.
        :type uri: unicode
        :param method: What will this URL be used to do: 'GET' the node,
        'PUT' or 'POST' to the node or 'DELETE' it
        :type method: unicode
        :param view: If this is a 'GET' which view of the node should the
        URL provide.
        :type view: unicode
        :param limit: If this is a container how many of the children should
        be returned? (None - Unlimited)
        :type limit: int, None
        :param next_uri: When getting a container we make repeated calls
        until all 'limit' children returned. next_uri tells the service what
        was the last child uri retrieved in the previous call.
        :type next_uri: unicode
        :param cutout: The cutout pattern to apply to the file at the service
        end: applies to view='cutout' only.
        :type cutout: str, None
        :param sort: node property to sort on
        :type sort: vos.NodeProperty, None
        :param order: Order of sorting, Ascending ('asc' - default) or
        Descending ('desc')
        :type order: unicode, None
        :param full_negotiation: Should we use the transfer UWS or do a GET
        and follow the redirect.
        :type full_negotiation: bool
        :param content_length - size of the file to put
        :type str
        :param md5_checksum - checksum of the file content
        :type str
        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        uri = self.fix_uri(uri)

        if sort is not None and not isinstance(sort, SortNodeProperty):
            raise TypeError('sort must be an instance of vos.NodeProperty Enum')
        if order not in [None, 'asc', 'desc']:
            raise ValueError('order must be either "asc" or "desc"')

        logger.debug("Getting URL for: " + str(uri))

        parts = urlparse(uri)
        if parts.scheme.startswith('http'):
            return [uri]

        endpoints = self.get_endpoints(uri)

        if not full_negotiation and method == 'GET' and view in ['data', 'cutout', 'header']:
            return self._get(uri)

        if not full_negotiation and method == 'PUT':
            return self._put(uri, content_length=content_length,
                             md5_checksum=md5_checksum)

        if (view == "cutout" and cutout is None) or (
                cutout is not None and view != "cutout"):
            raise ValueError(
                "For cutout, must specify a view=cutout and for view=cutout"
                "must specify cutout")

        if method == 'GET' and view not in ['data', 'cutout', 'header']:
            # This is a request for the URL of the Node, which returns an XML
            # document that describes the node.
            fields = {}
            if limit is not None:
                fields['limit'] = limit
            if sort is not None:
                fields['sort'] = sort.value
            if order is not None:
                fields['order'] = order
            if view is not None:
                fields['view'] = view
            if next_uri is not None:
                fields['uri'] = next_uri

            tmp_url = '{}/{}'.format(endpoints.nodes, parts.path.strip('/'))
            # include the parameters into the url. Use Request to get it right
            req = requests.Request(method, tmp_url, params=fields)
            prepped = req.prepare()
            url = prepped.url
            logger.debug('URL: {} ({})'.format(url, method))
            return url

        # This is the shortcut. We do a GET request on the service with the
        # parameters sent as arguments.

        direction = {'GET': 'pullFromVoSpace', 'PUT': 'pushToVoSpace'}
        urls = self.transfer(self.get_endpoints(uri).transfer, uri, direction[method], None, None)
        logger.debug('Transfer URLs: ' + ', '.join(urls))
        return urls

    def link(self, src_uri, link_uri):
        """Make link_uri point to src_uri.

        :param src_uri: the existing resource to link to
        :type src_uri: unicode
        :param link_uri: the vospace node to create that will be a link to
        src_uri
        :type link_uri: unicode

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the cadcutils.exceptions module
        """
        link_uri = self.fix_uri(link_uri)
        if "://" not in src_uri:
            src_uri = self.fix_uri(src_uri)

        # if the link_uri points at an existing directory then we try and
        # make a link into that directory
        # if self.isdir(link_uri):
        #     link_uri = os.path.join(link_uri, os.path.basename(src_uri))

        with nodeCache.volatile(src_uri), nodeCache.volatile(
                link_uri):
            link_node = Node(link_uri, node_type="vos:LinkNode")
            ElementTree.SubElement(link_node.node, "target").text = src_uri
        data = str(link_node)
        size = len(data)

        url = self.get_node_url(link_uri)
        logger.debug("Got linkNode URL: {0}".format(url))
        self.get_session(link_uri).put(
            url, data=data, headers={'size': str(size)})

    def move(self, src_uri, destination_uri):
        """Move src_uri to destination_uri.  If destination_uri is a
        containerNode then move src_uri into destination_uri

        :param src_uri: the VOSpace node to be moved.
        :type src_uri: unicode
        :param destination_uri: the VOSpace location to move to.
        :type destination_uri: unicode
        :return did the move succeed?
        :rtype bool
        """
        src_uri = self.fix_uri(src_uri)
        destination_uri = self.fix_uri(destination_uri)
        with nodeCache.volatile(src_uri), nodeCache.volatile(
                destination_uri):
            job_url = self.transfer(self.get_endpoints(src_uri).async_transfer,
                                    src_uri, destination_uri, view='move')
            # start the job
            self.get_session(src_uri).post(
                job_url + '/phase',
                allow_redirects=False,
                data='PHASE=RUN',
                headers={'Content-type': 'application/x-www-form-urlencoded'})
            return self.get_transfer_error(job_url, src_uri)

    def _get(self, uri):
        with nodeCache.volatile(uri):
            files_ep = self.get_endpoints(uri).files
            if not files_ep:
                return None
            file_path = urlparse(uri).path
            if not file_path:
                return None
            files_url = '{}{}'.format(files_ep, file_path)
            if self._fs_type:
                # files_url contains the bytes
                return files_url
            # remaining is for vault
            try:
                response = self.get_session(uri).get(files_url, allow_redirects=False)
                response.raise_for_status()
            except Exception:
                return None
            if response.status_code == 303:
                return response.headers.get('Location', None)
            return None

    def _get_soda_params(self, view=None, cutout=None):
        # returns HTTP header corresponding to the soda params
        result = {}
        if view == 'header':
            result['META'] = 'true'
        elif cutout:
            if cutout.strip().startswith('['):
                # pixel cutout
                result['SUB'] = cutout
            elif cutout.strip().startswith('CIRCLE'):
                # circle cutout
                result['CIRCLE'] = cutout.replace('CIRCLE=', '')
            else:
                # TODO add support for other SODA cutouts SUB, POL etc
                raise ValueError('Unknown cutout type: ' + cutout)
        return result

    def _put(self, uri, content_length=None, md5_checksum=None):
        with nodeCache.volatile(uri):
            return self.transfer(self.get_endpoints(uri).transfer,
                                 uri, "pushToVoSpace", view=None,
                                 content_length=content_length,
                                 md5_checksum=md5_checksum)

    def transfer(self, endpoint_url, uri, direction, view=None, cutout=None,
                 content_length=None, md5_checksum=None):
        """Build the transfer XML document
        :param endpoint_url: the URL of the endpoint to POST to
        :param direction: is this a pushToVoSpace or a pullFromVoSpace ?
        :param uri: the uri to transfer from or to VOSpace.
        :param view: which view of the node (data/default/cutout/etc.) is
        being transferred
        :param cutout: a special parameter added to the 'cutout' view
        request. e.g. '[0][1:10,1:10]'
        :param content_length: the size file to put
        :param md5_checksum: the md5 checksum of the content of the file

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        endpoints = self.get_endpoints(uri)

        trans = net.Transfer(self.get_session(uri))
        security_methods = []
        if endpoints.conn.subject.certificate:
            security_methods.append(
                SSO_SECURITY_METHODS['tls-with-certificate'])
        if endpoints.conn.subject.cookies:
            security_methods.append(SSO_SECURITY_METHODS['cookie'])
        if endpoints.conn.vo_token:
            security_methods.append(SSO_SECURITY_METHODS['token'])

        result = trans.transfer(endpoint_url, uri, direction, view, cutout,
                                security_methods=security_methods)
        # if this is a connection to the 'rc' server then we reverse the
        # urllist to test the fail-over process
        if urlparse(endpoints.nodes).netloc.startswith('rc') and \
                isinstance(result, list):
            result.reverse()
        return result

    def get_transfer_error(self, url, uri):
        """Follow a transfer URL to the Error message
        :param url: The URL of the transfer request that had the error.
        :param uri: The uri that we were trying to transfer (get or put).

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """

        trans = net.Transfer(self.get_session(uri))
        return trans.get_transfer_error(url, uri)

    def open(self, uri, mode=os.O_RDONLY, view=None, head=False, url=None,
             limit=None, next_uri=None, size=None, cutout=None,
             byte_range=None, sort=None, order=None,
             full_negotiation=False, possible_partial_read=False):
        """Create a VOFile connection to the specified uri or url.

        :rtype : VOFile
        :param uri: The uri of the VOSpace resource to create a connection to,
        override by specifying url
        :type uri: unicode, None
        :param mode: The mode os.O_RDONLY or os.O_WRONLY to open the
        connection with.
        :type mode: bit
        :param view: The view of the VOSpace resource, one of: default, data,
        cutout
        :type view: unicode, None
        :param head: Just return the http header of this request.
        :type head: bool
        :param url: Ignore the uri (ie don't look up the url using
        get_node_url) and just connect to this url
        :type url: unicode, None
        :param limit: limit response from vospace to this many child nodes.
        relevant for containerNode type
        :type limit: int, None
        :param next_uri: The  uri of the last child node returned by a
        previous request on a containerNode
        :type next_uri: unicode, None
        :param size: The size of file to expect or be put to VOSpace
        :type size: int, None
        :param cutout: The cutout pattern to use during a get
        :type cutout: unicode, None
        :param byte_range: The range of bytes to request, rather than getting
        the entire file.
        :type byte_range: unicode, None
        :param sort: node property to sort on
        :type sort: vos.NodeProperty, None
        :param order: Sorting order. Values: asc for ascending (default), desc
        for descending
        :type order: unicode, None
        :param full_negotiation: force this interaction to use the full UWS
        interaction to get the url for the resource
        :type full_negotiation: bool
        :param possible_partial_read:
        """

        # sometimes this is called with mode from ['w', 'r']
        # really that's an error, but I thought I'd just accept those are
        # os.O_RDONLY

        if isinstance(mode, str):
            mode = os.O_RDONLY

        # the url of the connection depends if we are 'getting', 'putting' or
        # 'posting'  data
        method = None
        if mode == os.O_RDONLY:
            method = "GET"
        elif mode & (os.O_WRONLY | os.O_CREAT):
            method = "PUT"
        elif mode & os.O_APPEND:
            method = "POST"
        elif mode & os.O_TRUNC:
            method = "DELETE"
        if head:
            method = "HEAD"
        if not method:
            raise OSError(errno.EOPNOTSUPP, "Invalid access mode", mode)

        if uri is not None and view in ['data', 'cutout']:
            # Check if this is a target node.
            try:
                node = self.get_node(uri)
                if node.type == "vos:LinkNode":
                    target = node.node.findtext(Node.TARGET)
                    logger.debug('{} is a link to {}'.format(node.uri, target))
                    if target is None:
                        raise OSError(errno.ENOENT, "No target for link")
                    else:
                        parts = urlparse(target)
                        if parts.scheme == 'vos':
                            # This is a link to another VOSpace node so lets
                            # open that instead.
                            return self.open(target, mode, view, head, url,
                                             limit,
                                             next_uri, size, cutout,
                                             byte_range, sort, order)
                        else:
                            # A target external link
                            # TODO Need a way of passing along authentication.
                            if cutout is not None:
                                target = "{0}?cutout={1}".format(target,
                                                                 cutout)
                            return VOFile(
                                [target],
                                self.get_session(uri),
                                method=method,
                                size=size,
                                byte_range=byte_range,
                                possible_partial_read=possible_partial_read)
            except OSError as ose:
                if ose.errno in [2, 404]:
                    pass
                else:
                    raise ose

        if url is None:
            url = self.get_node_url(uri, method=method, view=view,
                                    limit=limit, next_uri=next_uri,
                                    cutout=cutout, sort=sort, order=order,
                                    full_negotiation=full_negotiation)
            if url is None:
                raise OSError(errno.EREMOTE)

        return VOFile(url, self.get_endpoints(uri).conn, method=method,
                      size=size, byte_range=byte_range,
                      possible_partial_read=possible_partial_read)

    def add_props(self, node, recursive=False):
        """Given a node structure do a POST of the XML to the VOSpace to
           update the node properties

            Makes a new copy of current local state, then gets a copy of
            what's on the server and
            then updates server with differences.

           :param node: the Node object to add some properties to.

           :raises When a network problem occurs, it raises one of the
           HttpException exceptions declared in the
        cadcutils.exceptions module
           """
        new_props = copy.deepcopy(node.props)
        old_props = self.get_node(node.uri, force=True).props
        for prop in old_props:
            if prop in new_props and old_props[prop] == new_props[prop] and \
                            old_props[prop] is not None:
                del (new_props[prop])
        node.node = node.create(node.uri, node_type=node.type,
                                properties=new_props)
        # Now write these new properties to the node location.
        url = self.get_node_url(node.uri, method='GET')
        data = str(node)
        size = len(data)
        session = self.get_session(node.uri)
        if recursive:
            response = session.post(self.get_endpoints(node.uri).recursive_props,
                                    data=str(node), allow_redirects=False,
                                    headers={'Content-type': 'text/xml'})
            response.raise_for_status()
            if response.status_code != 303:
                raise RuntimeError('Unexpected response for running job: '
                                   + response.status_code)
            return self._run_recursive_job(session,
                                           response.headers['location'])
        else:
            session.post(url, headers={'size': str(size)}, data=data)
            return 1, 0

    def create(self, uri):
        """
        Create a (Container/Link/Data) Node on the VOSpace server.

        :param uri: the Node that we are going to create on the server.
        :type  uri: vos.Node

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        fixed_uri = self.fix_uri(uri)
        node = Node(fixed_uri)
        path = urlparse(fixed_uri).path
        url = '{}{}'.format(self.get_endpoints(fixed_uri).nodes, path)
        data = str(node)
        size = len(data)
        return Node(self.get_session(uri).put(
            url, data=data,
            headers={'size': str(size), 'Content-Type': 'text/xml'}).content)

    def update(self, node, recursive=False):
        """Updates the node properties on the server. For non-recursive
           updates, node's properties are updated on the server. For
           recursive updates, node should only contain the properties to
           be changed in the node itself as well as all its children.

           :param node: the node to update.
           :param recursive: should this update be applied to all children?
           (True/False)

            :raises When a network problem occurs, it raises one of the
            HttpException exceptions declared in the
            cadcutils.exceptions module
           """
        # Let's do this update using the async transfer method
        url = self.get_node_url(node.uri)
        endpoints = self.get_endpoints(node.uri)
        session = self.get_session(node.uri)
        if recursive:
            try:
                property_url = endpoints.recursive_props
            except KeyError as ex:
                logger.debug('recursive props endpoint does not exist: {0}'.
                             format(str(ex)))
                raise Exception('Operation not supported')
            logger.debug("prop URL: {0}".format(property_url))
            # quickly check target exists
            session.get(endpoints.nodes + urlparse(node.uri).path)
            response = session.post(endpoints.recursive_props,
                                    data=str(node), allow_redirects=False,
                                    headers={'Content-type': 'text/xml'})
            response.raise_for_status()
            if response.status_code != 303:
                raise RuntimeError('Unexpected response for running job: '
                                   + response.status_code)
            return self._run_recursive_job(session,
                                           response.headers['location'])
        else:
            resp = session.post(url, data=str(node), allow_redirects=False)
            logger.debug("update response: {0}".format(resp.content))
            resp.raise_for_status()
        return 1, 0

    def mkdir(self, uri):
        """
        Create a ContainerNode on the service.  Raise OSError(EEXIST) if the
        container exists.

        :param uri: The URI of the ContainerNode to create on the service.
        :type uri: unicode

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        uri = self.fix_uri(uri)
        node = Node(uri, node_type="vos:ContainerNode")
        url = self.get_node_url(uri)
        if isinstance(url, list) and len(url) > 0:
            url = url.pop(0)
        try:
            response = self.get_session(uri).put(
                url, data=str(node), headers={'Content-Type': 'text/xml'})
            response.raise_for_status()
        except HTTPError as http_error:
            if http_error.response.status_code != 409:
                raise http_error
            else:
                raise OSError(errno.EEXIST,
                              'ContainerNode {0} already exists'.format(uri))

    def delete(self, uri):
        """Delete the node
        :param uri: The (Container/Link/Data)Node to delete from the service.

        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        uri = self.fix_uri(uri)
        logger.debug("delete {0}".format(uri))
        with nodeCache.volatile(uri):
            url = self.get_node_url(uri, method='GET')
            if isinstance(url, list) and len(url) > 0:
                url = url.pop(0)
            response = self.get_session(uri).delete(url)
            response.raise_for_status()

    def recursive_delete(self, uri):
        """Delete the node and its content.
        :param uri: The (Container/Link/Data)Node to delete from the service.

        :returns: tuple of the form (successfull_deletes, failed_deletes)
        :raises When a network problem occurs, it raises one of the
        HttpException exceptions declared in the
        cadcutils.exceptions module
        """
        uri = self.fix_uri(uri)
        logger.debug("recursive delete {0}".format(uri))
        with nodeCache.volatile(uri):
            session = self.get_session(uri)
            # quickly check target exists
            self.get_node(uri)
            response = session.post(self.get_endpoints(uri).recursive_del, {'target': uri}, allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 303:
                raise RuntimeError('Unexpected response for running job: '
                                   + response.status_code)
            return self._run_recursive_job(session, response.headers['location'])

    def _run_recursive_job(self, session, url):
        # runs an already created recursive job and returns the number of
        # successfull and unsuccessfull actions
        logger.debug('POST: ' + url)
        response = session.post(url + '/phase', data={'phase': 'RUN'}, allow_redirects=False)
        if response.status_code != 303:
            raise RuntimeError('Unexpected response for running job: '
                               + response.status_code)

        # polling: WAIT will block for up to 6 sec or until phase change or if job is in
        # a terminal phase
        jobPoll = url + '?WAIT=6'
        count = 0
        done = False
        while not done and count < 100:  # max 100*6 = 600 sec polling
            logger.debug("poll: " + jobPoll)
            resp = session.get(jobPoll)
            resp.raise_for_status()
            xml_string = resp.content
            logging.debug('Job Document:{}'.format(xml_string))
            job_document = ElementTree.fromstring(xml_string)
            if job_document.find('uws:phase', UWS_NSMAP) is not None:
                phase = job_document.find('uws:phase', UWS_NSMAP).text
            else:
                raise RuntimeError('Cannot determine job phase')
            if phase.upper() in ['QUEUED', 'EXECUTING', 'SUSPENDED']:
                count += 1
            elif phase.upper() == 'ERROR':
                message = 'Failed'
                error_summary = job_document.find('uws:errorSummary', UWS_NSMAP)
                if (error_summary is not None and
                        error_summary.find('uws:message', UWS_NSMAP) is not None):
                    message = error_summary.find('uws:message', UWS_NSMAP).text
                raise RuntimeError(message)
            elif phase.upper() in ['COMPLETED', 'ABORTED']:
                results = job_document.find('uws:results', UWS_NSMAP)
                error_count = 0
                success_count = 0
                if results is not None:
                    results = results.findall('uws:result', UWS_NSMAP)
                    if results is not None:
                        for res in results:
                            if res.attrib['id'] == 'successcount':
                                success_count = res.attrib['{' + UWS_NSMAP['xlink'] + '}href'].split(':')[1]
                            elif res.attrib['id'] == 'errorcount':
                                error_count = res.attrib['{' + UWS_NSMAP['xlink'] + '}href'].split(':')[1]
                return success_count, error_count
            else:
                raise RuntimeError('Unknown job phase: ' + phase)

    def get_children_info(self, uri, sort=None, order=None, force=False):
        """Returns an iterator over tuples of (NodeName, Info dict)
        :param uri: the Node to get info about.
        :param sort: node property to sort on (vos.NodeProperty)
        :param order: order of sorting: 'asc' - default or 'desc'
        :param force: if True force the read from server otherwise use local
        cache
        """
        uri = self.fix_uri(uri)
        logger.debug(str(uri))
        node = self.get_node(uri, limit=0, force=force)
        logger.debug(str(node))
        while node.type == "vos:LinkNode":
            uri = node.target
            try:
                node = self.get_node(uri, limit=0, force=force)
            except Exception as exception:
                logger.error(str(exception))
                break
        if node.type in ["vos:DataNode", "vos:LinkNode"]:
            return [node]
        else:
            return node.get_children(self, sort, order, None)

    def get_info_list(self, uri):
        """Retrieve a list of tuples of (NodeName, Info dict).
        Similar to the method above except that information is loaded
        directly into memory.
        :param uri: the Node to get info about.
        """
        info_list = []
        uri = self.fix_uri(uri)
        logger.debug(str(uri))
        node = self.get_node(uri, limit=None, force=True)
        logger.debug(str(node))
        while node.type == "vos:LinkNode":
            uri = node.target
            try:
                node = self.get_node(uri, limit=None, force=True)
            except Exception as exception:
                logger.error(str(exception))
                break
        for thisNode in node.node_list:
            info_list.append(thisNode)
        if node.type in ["vos:DataNode", "vos:LinkNode"]:
            info_list.append(node)
        return info_list

    def listdir(self, uri, force=False):
        """
        Return a list with the content of the directory
        Follows LinksNodes to their destination location.
        Note: this method returns a list of children names. For larger
        directories, use get_children_info() to iterate through it and
        avoid loading the entire content into memory.

        :param force: don't use cached values, retrieve from service.
        :param uri: The ContainerNode to get a listing of.
        :rtype [unicode]
        """
        logger.debug(str(uri))
        node = self.get_node(uri, limit=0, force=force)
        while node.type == "vos:LinkNode":
            uri = node.target
            # logger.debug(uri)
            node = self.get_node(uri, limit=0, force=force)
        return [i.name for i in self.get_children_info(node.uri, force=force)]

    def _node_type(self, uri):
        """
        Recursively follow links until the base Node is found.
        :param uri: the VOSpace uri to recursively get the type of.
        :return: the type of Node
        :rtype: unicode
        """
        node = self.get_node(uri, limit=0)
        while node.type == "vos:LinkNode":
            uri = node.target
            if self.is_remote_file(uri):
                if uri.startswith("http"):
                    return 'vos:DataNode'
                node = self.get_node(uri, limit=0)
            else:
                return "vos:DataNode"
        return node.type

    def size(self, uri):
        node = self.get_node(uri, limit=0)
        while node.type == "vos:LinkNode":
            uri = node.target
            if self.is_remote_file(uri):
                node = self.get_node(uri, limit=0)
            else:
                return int(requests.head(uri).headers.get('Content-Length', 0))
        return node.get_info()['size']

    def isdir(self, uri):
        """
        Check to see if the given uri is or is a link to a containerNode.

        :param uri: a VOSpace Node URI to test.
        :rtype: bool
        """
        try:
            return self._node_type(uri) == "vos:ContainerNode"
        except exceptions.NotFoundException:
            return False

    def isfile(self, uri):
        """
        Check if the given uri is or is a link to a DataNode

        :param uri: the VOSpace Node URI to test.
        :rtype: bool
        """
        try:
            return self._node_type(uri) == "vos:DataNode"
        except OSError as ex:
            if ex.errno == errno.ENOENT:
                return False
            raise ex

    def access(self, uri, mode=os.O_RDONLY):
        """Test if the give VOSpace uri can be accessed in the way requested.

        :param uri:  a VOSpace location.
        :param mode: os.O_RDONLY
        """

        if mode == os.O_RDONLY:
            try:
                self.get_node(uri, limit=0, force=True)
            except (exceptions.NotFoundException,
                    exceptions.AlreadyExistsException,
                    exceptions.UnauthorizedException,
                    exceptions.ForbiddenException):
                return False

        return isinstance(self.open(uri, mode=mode), VOFile)

    def status(self, uri, code=None):
        """Check to see if this given uri points at a containerNode.

        This is done by checking the view=data header and seeing if you
        get an error.
        :param uri: the VOSpace (Container/Link/Data)Node to check access
        status on.
        :param code: NOT SUPPORTED.
        """
        if code:
            raise OSError(errno.ENOSYS,
                          "Use of 'code' option values no longer supported.")
        self.get_node(uri)
        return True

    # def get_job_status(self, url):
    #     """ Returns the status of a job
    #     :param url: the URL of the UWS job to get status of.
    #     :rtype: unicode
    #     """
    #     return VOFile(url, self.conn, method="GET",
    #                   follow_redirect=False).read()


class Md5File(object):
    """
    A wrapper to a file object that calculates the MD5 sum of the bytes
    that are being read or written.
    """

    def __init__(self, f, mode):
        self.file = open(f, mode)
        self._md5_checksum = hashlib.md5()

    def __enter__(self):
        return self

    def read(self, size):
        buffer = self.file.read(size)
        self._md5_checksum.update(buffer)
        return buffer

    def write(self, buffer):
        self._md5_checksum.update(buffer)
        self.file.write(buffer)
        self.file.flush()

    def __exit__(self, *args, **kwargs):
        if not self.file.closed:
            self.file.close()
        # clean up
        exit = getattr(self.file, '__exit__', None)
        if exit is not None:
            return exit(*args, **kwargs)
        else:
            exit = getattr(self.file, 'close',
                           None)
            if exit is not None:
                exit()

    def __getattr__(self, attr):
        return getattr(self.file, attr)

    def __iter__(self):
        return iter(self.file)

    @property
    def md5_checksum(self):
        return self._md5_checksum.hexdigest()
