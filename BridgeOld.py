# adapted from a proxy for another assignment, contains many unnecessary functions, but I didn't feel like removing them


# Place your imports here
import signal
import subprocess
import threading
from datetime import datetime
from optparse import OptionParser
import sys
from socket import *
import re
import logging
from enum import Enum
import time

cache_enabled = False
cache = {}
cache_lock = threading.Lock()

blocking_enabled = False
blocklist = []
blocking_lock = threading.Lock()


# Signal handler for pressing ctrl-c
def ctrl_c_pressed(s, f):
    sys.exit(0)


class ParseError(Enum):
    NOTIMPL = 1
    BADREQ = 2


def handle_client(client: socket):
    """
    Handles the operation requested by the client.
    :param client: Client socket
    """

    logging.debug("Client Connected")
    now = datetime.now()
    format_string = "%Y-%m-%d_%H:%M:%S"
    datetime_str = now.strftime(format_string)
    filename = datetime_str + ".wav"

    with client:
        request = receive_all_from_client(client)
        logging.debug(request)
        parse_error, request_host, request_port, request_path, request_headers, request_content = parse_request(request)
        if parse_error == ParseError.NOTIMPL:
            client.sendall(b"HTTP/1.0 501 Not Implemented" + eor)
            return
        elif parse_error == ParseError.BADREQ:
            client.sendall(b"HTTP/1.0 400 Bad Request" + eor)
            return
        if check_blocklist(request_host):
            client.sendall(b"HTTP/1.0 403 Forbidden" + eor)
            return

        with open(filename, "wb") as wav_file:
            wav_file.write(request_content)

        client.sendall(b"HTTP/1.0 200 OK" + eor)

    command = "./whisper -m models/ggml-small.en.bin -f " + filename + " -t 15 -nt -np"
    transcription = subprocess.check_output(command)

    host = gethostname()
    port = 4567
    with socket() as client_socket:
        client_socket.connect((host, port))  # connect to the server
        client_socket.sendall(transcription)


def cacheable(response: bytes) -> bool:
    """
    Checks if a response has a 200 status code.
    :param response: response in bytes
    :return: True if the response has a 200 status
    """
    response_pattern = re.compile(b"HTTP/1.. (\\d{3}) .*\r\n.*")
    logging.debug(response)
    status_code = re.search(response_pattern, response).group(1)
    logging.debug(status_code)
    if status_code == b"200":
        return True
    else:
        return False


def parse_request(message: bytes) -> (ParseError, str, int, str, dict, bytes):
    """
    Parses a request from the client and separates it into the correct pieces.
    It also handles the detection of a number of errors and the replacement
    of incorrect connection headers.
    :param message: The request from the client in bytes
    :return: Error code, host, port, path, headers
    """
    request_pattern = re.compile(b"(GET|HEAD|POST) (.+) HTTP/1.0\r\n((?:\\S+: .+\r\n)*)\r\n(.+)\r\n\r\n")
    host_pattern = re.compile(b"://([^:/]+)(?::([0-9]+))?(/\\S*)")
    header_pattern = re.compile(b"(.+):\\s+(.+)\r\n")

    if not re.fullmatch(request_pattern, message):
        return ParseError.BADREQ, None, None, None, None, None

    method, uri, headers, content = re.search(request_pattern, message).groups()
    if method == b'HEAD' or method == b'GET':
        return ParseError.NOTIMPL, None, None, None, None, None

    host_result = re.search(host_pattern, uri)
    if host_result is not None:
        host, request_port, path = host_result.groups()
    else:
        return ParseError.BADREQ, None, None, None, None, None

    if request_port is None:
        port = 80
    else:
        port = int(request_port)

    headers = {}
    for header in re.finditer(header_pattern, message):
        key, val = header.groups()
        if key == b"Proxy-Connection" or key == b"Connection":
            key = b"Connection"
            val = b"close"
        headers[key] = val
    if b"Connection" not in headers:
        headers[b"Connection"] = b"close"

    return None, host, port, path, headers, content


def receive_all_from_client(client: socket) -> bytes:
    """
    Receives an entire request from the client terminated with \r\n\r\n.
    :param client: Client socket
    :return: The entire request
    """
    temp_request = b""
    while not temp_request.endswith(eor):
        temp_request += client.recv(2048)
    return temp_request


def receive_all_from_server(request_skt: socket) -> bytes:
    """
    Receive an entire response from the server.
    :param request_skt: Server socket
    :return: the entire response
    """
    temp_result = b""
    while True:
        current_result = request_skt.recv(2048)
        if not current_result:
            break
        temp_result += current_result
    return temp_result


def make_host_string(host: bytes, port: int) -> bytes:
    """
    Combines a host and port number to make a representative host string (host:port).
    :param host: host
    :param port: port
    :return: formatted string
    """
    return host + b":" + str(port).encode()


def format_request(cached: bool, request_host: bytes, request_port: int, request_path: bytes,
                   request_headers: dict) -> bytes:
    """
    Re-formats the client's request for sending to the server.
    :param cached: Whether the response was cached and a conditional GET should be made
    :param request_host: Host of server
    :param request_port: Port of server
    :param request_path: Path requested
    :param request_headers: Headers of request
    :return: formatted request
    """
    if cached:
        formatted_time = read_cache(make_host_string(request_host, request_port), request_path)[1]
        request_headers[b"If-Modified-Since"] = formatted_time.encode()
    new_request = b""
    new_request += b"GET " + request_path + b" HTTP/1.0" + eol
    request_headers[b"Host"] = request_host
    for header in request_headers:
        new_request += b"%s: %s%s" % (header, request_headers[header], eol)
    new_request += eol
    return new_request


def read_cache(host_string: bytes, path: bytes) -> (bytes, bytes):
    """
    Reads an entry from the cache, returns None if no match is found.
    :param host_string: The host string of the server
    :param path: The path to the resource requested
    :return: Cached entry and the time it was placed in the cache
    """
    with cache_lock:
        if (host_string, path) in cache:
            return cache[(host_string, path)]
        else:
            return None


def write_cache(host_string: bytes, path: bytes, response: bytes):
    """
    Write an entry to the cache.
    :param host_string: The host string of the server
    :param path: The path to the resource requested
    :param response: The response from the server to cache
    """
    global cache
    logging.debug("write")
    current_time = time.gmtime()
    formatted_time = time.strftime("%a, %d %b %Y %H:%M:%S GMT", current_time)
    with cache_lock:
        cache[(host_string, path)] = (response, formatted_time)


def clear_cache():
    """
    Clear the cache.
    """
    global cache
    cache = {}


def check_blocklist(host: bytes) -> bool:
    """
    Checks if a host is in the blocklist.
    :param host: The host to check against
    :return: Whether the host is in the blocklist
    """
    if not blocking_enabled:
        return False
    with blocking_lock:
        logging.debug(b"host " + host)
        logging.debug(blocklist)
        for blocked_host in blocklist:
            if (blocked_host in host) or (host in blocked_host):
                return True
        return False


def write_blocklist(host: bytes):
    """
    Writes a new host to the blocklist.
    :param host: The new host
    """
    with blocking_lock:
        blocklist.append(host)


def remove_blocklist(host: bytes):
    """
    Removes a host from the blocklist.
    :param host: the host to remove
    """
    with blocking_lock:
        blocklist.remove(host)


def clear_blocklist():
    """
    Clears the blocklist
    """
    global blocklist
    blocklist = []


def handle_command(path: bytes) -> bool:
    """
    Handles any command to the proxy encoded in the requested path.
    :param path: the path requested by the client
    :return: whether a command was matched
    """
    global cache_enabled
    global blocking_enabled
    match path:
        case b"/proxy/cache/enable":
            cache_enabled = True
            return True
        case b"/proxy/cache/disable":
            cache_enabled = False
            return True
        case b"/proxy/cache/flush":
            clear_cache()
            return True
        case b"/proxy/blocklist/enable":
            blocking_enabled = True
            return True
        case b"/proxy/blocklist/disable":
            blocking_enabled = False
            return True
        case b"/proxy/blocklist/flush":
            clear_blocklist()
            return True

    blocklist_add = re.fullmatch(b"/proxy/blocklist/add/(.+)", path)
    if blocklist_add is not None:
        write_blocklist(blocklist_add.group(1))
        return True

    blocklist_remove = re.fullmatch(b"/proxy/blocklist/remove/(.+)", path)
    if blocklist_remove is not None:
        remove_blocklist(blocklist_remove.group(1))
        return True

    return False


def make_request(uri: (bytes, bytes), request: bytes) -> bytes:
    """
    Sends a request to the server.
    :param uri: the host and port
    :param request: the full request
    :return: the response from the server
    """
    with socket(AF_INET, SOCK_STREAM) as request_skt:
        request_skt.connect(uri)
        request_skt.sendall(request)
        return receive_all_from_server(request_skt)


logging.basicConfig(level=logging.DEBUG)

eol = b'\r\n'
eor = b'\r\n\r\n'
# Start of program execution
# Parse out the command line server address and port number to listen to
parser = OptionParser()
parser.add_option('-p', type='int', dest='serverPort')
parser.add_option('-a', type='string', dest='serverAddress')
(options, args) = parser.parse_args()

listen_port = options.serverPort
listen_address = options.serverAddress
if listen_address is None:
    listen_address = 'localhost'
if listen_port is None:
    listen_port = 2100

# Set up signal handling (ctrl-c)
signal.signal(signal.SIGINT, ctrl_c_pressed)

# Continually listen for new clients and pass them off to a new thread
with socket(AF_INET, SOCK_STREAM) as listen_skt:
    listen_skt.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    listen_skt.bind((listen_address, listen_port))
    listen_skt.listen()
    while True:
        skt, client_address = listen_skt.accept()
        threading.Thread(target=handle_client, args={skt}).start()
