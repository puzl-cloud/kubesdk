from typing import TypeVar
from urllib.parse import urlsplit


def host_from_url(url: str, include_port: bool = True) -> str | None:
    """
    Return the host (domain or IP) from a URL.
    - Works when the scheme is missing (e.g., 'example.com/path' or 'localhost:3000').
    - Returns None if no host can be determined (e.g., '/just/a/path' or 'mailto:...').
    - If include_port=True, appends ':port' (or '[ipv6]:port') when present.
    """
    if not url:
        return None
    u = url.strip()
    if not u:
        return None

    parsed_u = urlsplit(u)
    if not parsed_u.netloc and '://' not in u:
        head = u.split('/', 1)[0]  # part before the first '/'
        # If the part before '/' looks like host:digits, treat as schemeless netloc
        if (':' in head and head.rsplit(':', 1)[1].isdigit()) or not parsed_u.scheme:
            parsed_u = urlsplit('//' + u)

    # If there's no netloc and no '://', treat as schemeless; also handle 'host:1234' (path all digits)
    if not parsed_u.netloc and '://' not in u and (not parsed_u.scheme or (parsed_u.path and parsed_u.path.isdigit())):
        parsed_u = urlsplit('//' + u)

    host = parsed_u.hostname  # lowercased, IPv6 without brackets
    if not host:
        return None

    if include_port and parsed_u.port is not None:
        # Bracket IPv6 when adding a port
        if ':' in host and not host.startswith('['):
            return f'[{host}]:{parsed_u.port}'
        return f'{host}:{parsed_u.port}'
    return host


def join_host_port(host: str, port: str | int) -> str:
    template = "%s:%s"
    host_requires_bracketing = ':' in host or '%' in host
    if host_requires_bracketing:
        template = "[%s]:%s"
    return template % (host, port)


_T = TypeVar("_T")


def normalize_dict_keys(obj: _T) -> _T:
    if isinstance(obj, dict):
        return {
            (key.replace('-', '_') if isinstance(key, str) else key): normalize_dict_keys(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [normalize_dict_keys(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(normalize_dict_keys(item) for item in obj)
    return obj
