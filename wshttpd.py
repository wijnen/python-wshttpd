# Python module for serving WebSockets and web pages.
# vim: set fileencoding=utf-8 foldmethod=marker :

# {{{ Copyright 2013-2014 Bas Wijnen <wijnen@debian.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# }}}

# See the example server for how to use this module.

# imports.  {{{
import network
from network import fgloop, bgloop, endloop
import os
import re
import sys
import urlparse
import urllib
import base64
import hashlib
import struct
import json
# }}}

known_codes = {	# {{{
		100: 'Continue', 101: 'Switching Protocols',
		200: 'OK', 201: 'Created', 202: 'Accepted', 203: 'Non-Authorative Information', 204: 'No Content', 205: 'Reset Content', 206: 'Partial Content',
		300: 'Multiple Choices', 301: 'Moved Permanently', 302: 'Found', 303: 'See Other', 304: 'Not Modified', 305: 'Use Proxy', 307: 'Temporary Redirect',
		400: 'Bad Request', 401: 'Unauthorized', 402: 'Payment Required', 403: 'Forbidden', 404: 'Not Found',
			405: 'Method Not Allowed', 406: 'Not Acceptable', 407: 'Proxy Authentication Required', 408: 'Request Timeout', 409: 'Conflict',
			410: 'Gone', 411: 'Length Required', 412: 'Precondition Failed', 413: 'Request Entity Too Large', 414: 'Request-URI Too Long',
			415: 'Unsupoported Media Type', 416: 'Requested Range Not Satisfiable', 417: 'Expectation Failed',
		500: 'Internal Server Error', 501: 'Not Implemented', 502: 'Bad Gateway', 503: 'Service Unavailable', 504: 'Gateway Timeout'}
# }}}

class Websocket: # {{{
	def __init__ (self, port, url = '/', recv = None, method = 'GET', user = None, password = None, extra = {}, socket = None, mask = (None, True), websockets = None, data = None, *a, **ka): # {{{
		self.recv = recv
		if socket is None:
			socket = network.Socket (port, *a, **ka)
		hdrdata = ''
		if url is not None:
			elist = []
			for e in extra:
				elist.append ('%s: %s\r\n' % (e, extra[e]))
			socket.send ('''\
%s %s HTTP/1.1\r
Connection: Upgrade\r
Upgrade: websocket\r
Sec-WebSocket-Key: 0\r
%s%s\r
''' % (method, url, '' if user is None else base64.b64encode (user + ':' + password) + '\r\n', ''.join (elist)))
			while '\n' not in hdrdata:
				r = socket.recv ()
				if r == '':
					raise EOFError ('EOF while reading reply')
				hdrdata += r
			pos = hdrdata.index ('\n')
			assert int (hdrdata[:pos].split ()[1]) == 101
			hdrdata = hdrdata[pos + 1:]
			data = {}
			while True:
				while '\n' not in hdrdata:
					r = socket.recv ()
					if r == '':
						raise EOFError ('EOF while reading reply')
					hdrdata += r
				pos = hdrdata.index ('\n')
				line = hdrdata[:pos].strip ()
				hdrdata = hdrdata[pos + 1:]
				if line == '':
					break
				key, value = [x.strip () for x in line.split (':', 1)]
				data[key] = value
		self.socket = socket
		self.mask = mask
		self.websockets = websockets
		self.data = data
		self.websocket_buffer = ''
		self.websocket_fragments = ''
		self._is_closed = False
		self._pong = True	# If false, we're waiting for a pong.
		self.socket.read (self._websocket_read)
		def disconnect (data):
			if not self._is_closed:
				self._is_closed = True
				if self.websockets is not None:
					self.websockets.remove (self)
				self.closed ()
			return ''
		if self.websockets is not None:
			self.websockets.add (self)
		self.socket.disconnect_cb (disconnect)
		self.opened ()
		if hdrdata != '':
			self._websocket_read (hdrdata)
	# }}}
	def _websocket_read (self, data, sync = False):	# {{{
		#print ('received: ' + repr (data))
		self.websocket_buffer += data
		if ord (self.websocket_buffer[0]) & 0x70:
			#print (repr (self.websocket_buffer))
			# Protocol error.
			print ('extension stuff, not supported!')
			self.socket.close ()
			return None
		if len (self.websocket_buffer) < 2:
			# Not enough data for length bytes.
			print ('no length yet')
			return None
		b = ord (self.websocket_buffer[1])
		have_mask = bool (b & 0x80)
		b &= 0x7f
		if have_mask and self.mask[0] is True or not have_mask and self.mask[0] is False:
			# Protocol error.
			print ('mask error')
			self.socket.close ()
			return None
		if b == 126 and len (self.websocket_buffer) < 4:
			# Not enough data for length bytes.
			print ('no 2 length yet')
			return None
		if b == 127 and len (self.websocket_buffer) < 10:
			# Not enough data for length bytes.
			print ('no 4 length yet')
			return None
		if b == 127:
			l = struct.unpack ('!Q', self.websocket_buffer[2:10])[0]
			pos = 10
		elif b == 126:
			l = struct.unpack ('!H', self.websocket_buffer[2:4])[0]
			pos = 4
		else:
			l = b
			pos = 2
		if len (self.websocket_buffer) < pos + (4 if have_mask else 0) + l:
			# Not enough data for packet.
			print ('no packet yet')
			return None
		opcode = ord (self.websocket_buffer[0]) & 0xf
		if have_mask:
			mask = [ord (x) for x in self.websocket_buffer[pos:pos + 4]]
			pos += 4
			data = self.websocket_buffer[pos:]
			# The following is slow!
			# Don't do it if the mask is 0; this is always true if talking to another program using this module.
			if mask != [0, 0, 0, 0]:
				data = ''.join ([chr (ord (x) ^ mask[i & 3]) for i, x in enumerate (data)])
		else:
			data = self.websocket_buffer[pos:]
		if (ord (self.websocket_buffer[0]) & 0x80) != 0x80:
			# fragment found; not last.
			if opcode != 0:
				# Protocol error.
				print ('invalid fragment')
				self.socket.close ()
				return None
			self.websocket_fragments += data
			print ('fragment recorded')
			return None
		# Complete frame has been received.
		self.websocket_buffer = ''
		data = self.websocket_fragments + data
		self.websocket_fragments = ''
		if opcode == 8:
			# Connection close request.
			self.close ()
			return None
		if opcode == 9:
			# Ping.
			self.send (data, 10)	# Pong
			return None
		if opcode == 10:
			# Pong.
			self._pong = True
			return None
		if opcode == 1:
			# Text.
			data = unicode (data, 'utf-8', 'replace')
			#print ('text')
			if sync:
				#print ('sync')
				return data
			if self.recv:
				#print ('async')
				self.recv (self, data)
			else:
				print ('warning: ignoring incoming websocket frame')
		if opcode == 2:
			# Binary.
			if sync:
				return data
			if self.recv:
				self.recv (self, data)
			else:
				print ('warning: ignoring incoming websocket frame (binary)')
	# }}}
	def send (self, data, opcode = 1):	# Send a WebSocket frame.  {{{
		#print ('websend:' + repr (data))
		assert opcode in (0, 1, 2, 8, 9, 10)
		if self._is_closed:
			return None
		if isinstance (data, unicode):
			data = data.encode ('utf-8')
		if self.mask[1]:
			maskchar = 0x80
			# Masks are stupid, but the standard requires them.  Don't waste time on encoding (or decoding, if also using this module).
			mask = '\0\0\0\0'
		else:
			maskchar = 0
			mask = ''
		if len (data) < 126:
			l = chr (maskchar | len (data))
		elif len (data) < 1 << 16:
			l = chr (maskchar | 126) + struct.pack ('!H', len (data))
		else:
			l = chr (maskchar | 127) + struct.pack ('!Q', len (data))
		try:
			self.socket.send (chr (0x80 | opcode) + l + mask + data)
		except:
			# Something went wrong; close the socket (in case it wasn't yet).
			self.socket.close ()
		if opcode == 8:
			self.socket.close ()
	# }}}
	def ping (self, data = ''): # Send a ping; return False if no pong was seen for previous ping.  {{{
		if not self._pong:
			return False
		self._pong = False
		self.send (data, opcode = 9)
		return True
	# }}}
	def close (self):	# Close a WebSocket.  (Use self.socket.close for other connections.)  {{{
		self.send ('', 8)
		self.socket.close ()
	# }}}
	def opened (self): # {{{
		pass
	# }}}
	def closed (self): # {{{
		pass
	# }}}
# }}}

class RPCWebsocket (Websocket): # {{{
	def __init__ (self, port, recv = None, *a, **ka): # {{{
		Websocket.__init__ (self, port, recv = RPCWebsocket.recv, *a, **ka)
		self.target = recv (self) if recv is not None else None
		#print ('init:' + repr (recv) + ',' + repr (self.target))
	# }}}
	class wrapper: # {{{
		def __init__ (self, base, attr): # {{{
			self.base = base
			self.attr = attr
		# }}}
		def __call__ (self, *a, **ka): # {{{
			self.base.send ('call', (self.attr, a, ka))
			ret = []
			while True:
				while True:
					data = self.base._websocket_read (self.base.socket.recv (), True)
					if data is not None:
						break
				ret = self.base.parse_frame (data)
				if ret[0] == 'return':
					return ret[1]
				# Async event crossed our call; respond to it.
				self.base.recv (self.base, data)
		# }}}
		def __getitem__ (self, *a, **ka): # {{{
			self.base.send ('event', (self.attr, a, ka))
		# }}}
	# }}}
	def send (self, type, object): # {{{
		#print ('sending:' + repr (type) + repr (object))
		Websocket.send (self, json.dumps ((type, object)))
	# }}}
	def parse_frame (self, frame): # {{{
		# Don't choke on Chrome's junk at the end of packets.
		data = json.JSONDecoder ().raw_decode (frame)[0]
		if type (data) is not list or len (data) != 2 or type (data[0]) is not unicode:
			print ('invalid frame %s' % repr (data))
			return (None, 'invalid frame')
		if data[0] in (u'event', u'call'):
			if not hasattr (self.target, data[1][0]) or not callable (getattr (self.target, data[1][0])):
				print ('invalid call or event frame %s' % repr (data))
				return (None, 'invalid frame')
		elif data[0] not in (u'error', u'return'):
			#self.send ('error', 'invalid frame')
			print ('invalid frame type %s' % repr (data))
			return (None, 'invalid frame')
		return data
	# }}}
	def recv (self, frame): # {{{
		#print ('recv/' + repr (self.target))
		data = self.parse_frame (frame)
		#print data
		if data[0] is None:
			return
		elif data[0] == 'error':
			raise ValueError (data[1])
		try:
			if data[0] == 'call':
				#print (repr (self.target) + repr (data))
				self.send ('return', getattr (self.target, data[1][0]) (*data[1][1], **data[1][2]))
			elif data[0] == 'event':
				getattr (self.target, data[1][0]) (*data[1][1], **data[1][2])
			else:
				raise ValueError ('invalid RPC command')
		except:
			self.send ('error', str (sys.exc_value))
	# }}}
	def __getattr__ (self, attr): # {{{
		if attr.startswith ('_'):
			raise AttributeError ('invalid RPC function name')
		return RPCWebsocket.wrapper (self, attr)
	# }}}
# }}}

if network.have_glib: # {{{
	class Httpd_connection:	# {{{
		# Internal functions.  {{{
		def __init__ (self, server, socket, httpdirs, websocket = Websocket): # {{{
			self.server = server
			self.socket = socket
			self.httpdirs = httpdirs
			self.websocket = websocket
			self.headers = {}
			self.address = None
			self.socket.disconnect_cb (lambda data: '')	# Ignore disconnect until it is a WebSocket.
			self.socket.readlines (self._line)
			#sys.stderr.write ('Debug: new connection from %s\n' % repr (self.socket.remote))
		# }}}
		def _line (self, l):	# {{{
			#sys.stderr.write ('Debug: Received line: %s\n' % l)
			if self.address is not None:
				if not l.strip ():
					self._handle_headers ()
					return
				key, value = l.split (':', 1)
				self.headers[key] = value.strip ()
				return
			else:
				try:
					self.method, url, self.standard = l.split ()
					self.address = urlparse.urlparse (url)
					self.query = urlparse.parse_qs (self.address.query)
				except:
					self.reply (400)
					self.socket.close ()
				return
		# }}}
		def _handle_headers (self):	# {{{
			is_websocket = 'Connection' in self.headers and 'Upgrade' in self.headers and 'Upgrade' in self.headers['Connection'] and 'websocket' in self.headers['Upgrade']
			self.data = {}
			msg = self.server.auth_message (self, is_websocket) if callable (self.server.auth_message) else self.server.auth_message
			if msg:
				if 'Authorization' not in self.headers:
					self.reply (401, headers = {'WWW-Authenticate': 'Basic realm="%s"' % msg.replace ('\n', ' ').replace ('\r', ' ').replace ('"', "'")})
					if 'Content-Length' not in self.headers or self.headers['Content-Length'].strip () != '0':
						self.socket.close ()
					return
				else:
					auth = self.headers['Authorization'].split (None, 1)
					if auth[0] != 'Basic':
						self.reply (400)
						self.socket.close ()
						return
					pwdata = base64.b64decode (auth[1]).split (':', 1)
					if len (pwdata) != 2:
						self.reply (400)
						self.socket.close ()
						return
					self.data['user'] = pwdata[0]
					self.data['password'] = pwdata[1]
					if not self.authenticate (self):
						self.reply (401, headers = {'WWW-Authenticate': 'Basic realm="%s"' % msg.replace ('\n', ' ').replace ('\r', ' ').replace ('"', "'")})
						if 'Content-Length' not in self.headers or self.headers['Content-Length'].strip () != '0':
							self.socket.close ()
						return
			if not is_websocket:
				self.body = self.socket.unread ()
				try:
					self.page ()
				except:
					sys.stderr.write ('exception: %s\n' % repr (sys.exc_value))
					self.reply (500)
				self.socket.close ()
				return
			# Websocket.
			if self.method != 'GET' or 'Sec-WebSocket-Key' not in self.headers:
				self.reply (400)
				self.socket.close ()
				return
			newkey = base64.b64encode (hashlib.sha1 (self.headers['Sec-WebSocket-Key'].strip () + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest ())
			headers = {'Sec-WebSocket-Accept': newkey, 'Connection': 'Upgrade', 'Upgrade': 'websocket', 'Sec-WebSocket-Version': '13'}
			self.reply (101, None, None, headers)
			self.websocket (None, recv = self.server.recv, url = None, socket = self.socket, mask = (None, False), websockets = self.server.websockets, data = self.data)
		# }}}
		def _reply_websocket (self, message, content_type):	# {{{
			m = ''
			e = 0
			protocol = 'wss://' if hasattr (self.socket.socket, 'ssl_version') else 'ws://'
			host = self.headers['Host']
			for match in re.finditer (self.server.websocket_re, message):
				g = match.groups ()
				if len (g) > 0 and g[0]:
					extra = ' + ' + g[0]
				else:
					extra = ''
				m += message[e:match.start ()] + '''function () {
			if (window.hasOwnProperty ('MozWebSocket'))
				return new MozWebSocket ('%s%s'%s);
			else
				return new WebSocket ('%s%s'%s);
		} ()''' % (protocol, host, extra, protocol, host, extra)
				e = match.end ()
			m += message[e:]
			self.reply (200, m, content_type)
		# }}}
		# }}}
		# The following functions can be called by the overloaded page function. {{{
		def reply_html (self, message):	# {{{
			self._reply_websocket (message, 'text/html;charset=utf8')
		# }}}
		def reply_js (self, message):	# {{{
			self._reply_websocket (message, 'application/javascript;charset=utf8')
		# }}}
		def reply_css (self, message):	# {{{
			self.reply (200, message, 'text/css;charset=utf8')
		# }}}
		def reply (self, code, message = None, content_type = None, headers = None):	# Send HTTP status code and headers, and optionally a message.  {{{
			assert code in known_codes
			#sys.stderr.write ('Debug: sending reply %d %s for %s\n' % (code, known_codes[code], self.address.path))
			self.socket.send ('HTTP/1.1 %d %s\r\n' % (code, known_codes[code]))
			if headers is None:
				headers = {}
			if message is None and code != 101:
				assert content_type is None
				content_type = 'text/html;charset=utf-8'
				message = '<!DOCTYPE html><html><head><title>%s: %s</title></head><body><h1>%s: %s</h1></body></html>' % (code, known_codes[code], code, known_codes[code])
			if content_type is not None:
				headers['Content-Type'] = content_type
				headers['Content-Length'] = len (message)
			else:
				assert code == 101
				message = ''
			self.socket.send (''.join (['%s: %s\r\n' % (x, headers[x]) for x in headers]) + '\r\n' + message)
		# }}}
		# }}}
		# If httpdirs is not given, or special handling is desired, this can be overloaded.
		def page (self):	# A non-WebSocket page was requested.  Use self.address, self.method, self.query, self.headers and self.body (which may be incomplete) to find out more.  {{{
			if self.httpdirs is None:
				self.reply (501)
				return
			if self.address.path == '/':
				address = 'index'
			else:
				address = '/' + self.address.path + '/'
				while '/../' in address:
					# Don't handle this; just ignore it.
					pos = address.index ('/../')
					address = address[:pos] + address[pos + 3:]
				address = address[1:-1]
			if '.' in address:
				base, ext = address.rsplit ('.', 1)
				if ext not in self.exts:
					self.reply (404)
					return
				for d in self.httpdirs:
					filename = os.path.join (d, base + os.extsep + ext)
					if os.path.exists (filename):
						break
				else:
					self.reply (404)
					return
			else:
				base = address
				for ext in self.exts:
					for d in self.httpdirs:
						filename = os.path.join (d, base + os.extsep + ext)
						if os.path.exists (filename):
							break
					else:
						continue
					break
				else:
					self.reply (404)
					return
			return self.exts[ext] (self, open (filename).read ())
		# }}}
	# }}}
	class Httpd: # {{{
		def __init__ (self, port, recv = None, http_connection = Httpd_connection, httpdirs = None, server = None, *a, **ka): # {{{
			self.recv = recv
			self.http_connection = http_connection
			self.httpdirs = httpdirs
			self.websocket_re = r'#WEBSOCKET(?:\+(.*?))?#'
			# Initial extensions which are handled from httpdirs; others can be added by the user.
			self.exts = {
					'html': http_connection.reply_html,
					'js': http_connection.reply_js,
					'css': http_connection.reply_css
			}
			self.websockets = set ()
			if server is None:
				self.server = network.Server (port, self, *a, **ka)
			else:
				self.server = server
		# }}}
		def __call__ (self, socket): # {{{
			return self.http_connection (self, socket, self.httpdirs)
		# }}}
		def handle_ext (ext, mime): # {{{
			self.exts[ext] = lambda socket, message: http_connection.reply (socket, 200, message, mime)
		# }}}
		# Authentication. {{{
		# To use authentication, set auth_message to a static message
		# or define it as a method which returns a message.  The method
		# is called with two arguments, http_connection and is_websocket.
		# If it is or returns something non-False, authenticate will be
		# called, which should return a bool.  If it returns False, the
		# connection will be rejected without notifying the program.
		#
		# http_connection.data is a dict which contains the items 'user' and
		# 'password', set to their given values.  This dict may be
		# changed by authenticate and is passed to the websocket.
		# Apart from filling the initial contents, this module does not
		# touch it.  Note that http_connection.data is empty when
		# auth_message is called.  'user' and 'password' will be
		# overwritten before authenticate is called, but other items
		# can be added at will.
		#
		# ***********************
		# NOTE REGARDING SECURITY
		# ***********************
		# The module uses plain text authentication.  Anyone capable of
		# seeing the data can read the usernames and passwords.
		# Therefore, if you want authentication, you will also want to
		# use TLS to encrypt the connection.
		auth_message = None
		def authenticate (self, connection): # {{{
			return True
		# }}}
		# }}}
	# }}}
	class RPChttpd (Httpd): # {{{
		class RPCconnection (Httpd_connection):
			def __init__ (self, *a, **ka):
				Httpd_connection.__init__ (self, websocket = RPCWebsocket, *a, **ka)
		def __init__ (self, port, target, *a, **ka): # {{{
			Httpd.__init__ (self, port, target, RPChttpd.RPCconnection, *a, **ka)
		# }}}
	# }}}
# }}}
