"""
Gree protocol/network logic for Home Assistant integration.
"""

# Standard library imports
import asyncio
import base64
import ipaddress
import logging
import select
import socket
import struct
import time
from contextlib import suppress

try:
    import fcntl
except ImportError:
    fcntl = None

# Third-party imports
try:
    import simplejson
except ImportError:
    import json as simplejson
from Crypto.Cipher import AES

# Home Assistant imports
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_MAC
from homeassistant.components.network import async_get_ipv4_broadcast_addresses

# Local imports
from .const import CONF_ENCRYPTION_KEY

_LOGGER = logging.getLogger(__name__)

GCM_IV = b"\x54\x40\x78\x44\x49\x67\x5a\x51\x6c\x5e\x63\x13"
GCM_ADD = b"qualcomm-test"
GENERIC_DEVICE_KEY = b"{yxAHAY_Lm6pbC/<"
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
SIOCGIFBRDADDR = 0x8919


async def FetchResult(cipher, ip_addr, port, json_data, max_retries=8):
    """Send a request to a Gree device and fetch the result, with retries and timeouts."""

    _LOGGER.debug(f"Fetching device at: {ip_addr}:{port}, data sent: {json_data})")

    timeout = 2

    for attempt in range(max_retries):
        clientSock = None
        try:
            clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            clientSock.settimeout(timeout)

            # Send data to device
            clientSock.sendto(bytes(json_data, "utf-8"), (ip_addr, port))

            # Receive response with event loop yielding
            data, _ = await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, clientSock.recvfrom, 64000), timeout=timeout)

            # Parse and decrypt response
            received_json = simplejson.loads(data)
            pack = received_json["pack"]
            decoded_pack = base64.b64decode(pack)
            decrypted_pack = cipher.decrypt(decoded_pack)

            cipher.verify(base64.b64decode(received_json["tag"]))

            # Clean up response data
            decoded_text = decrypted_pack.decode("utf-8")
            # Remove null bytes and trailing data after last }
            clean_text = decoded_text.replace("\x0f", "")
            last_brace = clean_text.rindex("}")
            clean_text = clean_text[: last_brace + 1]

            result = simplejson.loads(clean_text)

            _LOGGER.debug(f"Successfully received response on attempt {attempt + 1}")
            return result

        except Exception as e:
            if attempt == max_retries - 1:
                error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else f"{type(e).__name__}"
                _LOGGER.error(f"All {max_retries} attempts failed for {ip_addr}:{port}. Error: {error_msg}")
                raise

        finally:
            if clientSock:
                try:
                    clientSock.close()
                except Exception as e:
                    _LOGGER.debug(f"Error closing socket: {str(e)}")

        # Progressive backoff before retry
        if attempt < max_retries - 1:
            await asyncio.sleep(0.5 + (attempt * 0.3))  # 0.5s, 0.8s, 1.1s, 1.4s, 1.7s, 2.0s, 2.3s


def _get_ioctl_ipv4_address(sock: socket.socket, ifname: str, request: int) -> str | None:
    """Fetch an IPv4 address for an interface using ioctl."""
    if fcntl is None:
        return None

    ifreq = struct.pack("256s", ifname[:15].encode("utf-8"))
    try:
        result = fcntl.ioctl(sock.fileno(), request, ifreq)
    except OSError:
        return None

    return socket.inet_ntoa(result[20:24])


def _get_linux_ipv4_bind_targets() -> list[tuple[str, str, str]]:
    """Return (source_ip, broadcast_ip, interface_name) for active IPv4 interfaces."""
    if fcntl is None or not hasattr(socket, "if_nameindex"):
        return []

    targets: list[tuple[str, str, str]] = []
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        for _, ifname in socket.if_nameindex():
            ifreq = struct.pack("256s", ifname[:15].encode("utf-8"))
            try:
                flags = struct.unpack(
                    "H",
                    fcntl.ioctl(probe_socket.fileno(), SIOCGIFFLAGS, ifreq)[16:18],
                )[0]
            except OSError:
                continue

            if not (flags & IFF_UP) or (flags & IFF_LOOPBACK):
                continue

            source_ip = _get_ioctl_ipv4_address(probe_socket, ifname, SIOCGIFADDR)
            broadcast_ip = _get_ioctl_ipv4_address(probe_socket, ifname, SIOCGIFBRDADDR)
            if not source_ip or not broadcast_ip:
                continue

            targets.append((source_ip, broadcast_ip, ifname))
    finally:
        probe_socket.close()

    return targets


def _broadcast_matches_source(source_ip: str, broadcast_ip: str) -> bool:
    """Check if a broadcast address belongs to a source interface's private range."""
    try:
        source = ipaddress.ip_address(source_ip)
        broadcast = ipaddress.ip_address(broadcast_ip)
    except ValueError:
        return False

    if source.version != 4 or broadcast.version != 4:
        return False

    if int(broadcast) == 0xFFFFFFFF:
        return True

    if source in ipaddress.ip_network("10.0.0.0/8"):
        return broadcast in ipaddress.ip_network("10.0.0.0/8")
    if source in ipaddress.ip_network("172.16.0.0/12"):
        return broadcast in ipaddress.ip_network("172.16.0.0/12")
    if source in ipaddress.ip_network("192.168.0.0/16"):
        return broadcast in ipaddress.ip_network("192.168.0.0/16")

    return False


def _build_discovery_sockets(
    interface_targets: list[tuple[str, str, str]],
) -> list[tuple[socket.socket, list[str], str]]:
    """Create one UDP broadcast socket per interface target."""
    sockets: list[tuple[socket.socket, list[str], str]] = []

    for source_ip, interface_broadcast, ifname in interface_targets:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((source_ip, 0))
        sockets.append((sock, [interface_broadcast, "255.255.255.255"], ifname))

    if sockets:
        return sockets

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sockets.append((sock, ["255.255.255.255"], "default"))
    return sockets


async def test_connection(config):
    """Test connection to a Gree device."""

    ip_addr = config[CONF_HOST]
    port = config[CONF_PORT]
    encryption_key = config[CONF_ENCRYPTION_KEY]

    mac_addr = config.get(CONF_MAC).encode().replace(b":", b"").replace(b"-", b"").decode("utf-8").lower()

    _LOGGER.debug(f"test_connection: host={ip_addr}, port={port}, mac={mac_addr}, encryption_key={encryption_key}")

    try:
        key = await GetDeviceKeyGCM(mac_addr, ip_addr, port)
        _LOGGER.debug(f"test_connection: Got device key: {key}")
        return key is not None
    except Exception as e:
        _LOGGER.error(f"Gree device at {ip_addr} is unreachable: {type(e).__name__}: {e}", exc_info=True)
        return False


def GetGCMCipher(key):
    cipher = AES.new(key, AES.MODE_GCM, nonce=GCM_IV)
    cipher.update(GCM_ADD)
    return cipher


def EncryptGCM(key, plaintext):
    cipher = GetGCMCipher(key)
    encrypted_data, tag = cipher.encrypt_and_digest(plaintext.encode("utf8"))
    pack = base64.b64encode(encrypted_data).decode("utf-8")
    tag = base64.b64encode(tag).decode("utf-8")
    return (pack, tag)


async def GetDeviceKeyGCM(mac_addr, ip_addr, port, max_retries=8):
    _LOGGER.debug("Retrieving HVAC encryption key (GCM)")
    plaintext = f'{{"cid":"{mac_addr}", "mac":"{mac_addr}","t":"bind","uid":0}}'
    pack, tag = EncryptGCM(GENERIC_DEVICE_KEY, plaintext)
    jsonPayloadToSend = f'{{"cid": "app","i": 1,"pack": "{pack}","t":"pack","tcid":"{mac_addr}","uid": 0, "tag" : "{tag}"}}'
    try:
        result = await FetchResult(GetGCMCipher(GENERIC_DEVICE_KEY), ip_addr, port, jsonPayloadToSend, max_retries=max_retries)
        _LOGGER.debug(f"GetDeviceKeyGCM: FetchResult: {result}")
        key = result["key"].encode("utf8")
    except Exception:
        _LOGGER.debug("Error getting device encryption key!")
        return None
    else:
        _LOGGER.debug(f"Fetched device encryption key: {str(key)}")
        return key


async def discover_gree_devices(hass, timeout=5):
    """Discover Gree devices on the local network using UDP broadcast."""
    _LOGGER.debug("Starting Gree device discovery...")

    BROADCAST_PORT = 7000
    DISCOVERY_MESSAGE = b'{"t":"scan"}'

    devices = []
    seen_device_ids: set[tuple[str, str]] = set()
    sockets: list[tuple[socket.socket, list[str], str]] = []

    try:
        interface_targets = _get_linux_ipv4_bind_targets()
        _LOGGER.debug(f"Discovered IPv4 interfaces for scan: {interface_targets}")

        # Default broadcast addresses to try
        broadcast_addresses = [
            "255.255.255.255",  # Limited broadcast
            "192.168.255.255",  # /16 broadcast for 192.168.x.x networks
            "10.255.255.255",  # /8 broadcast for 10.x.x.x networks
            "172.31.255.255",  # /12 broadcast for 172.16-31.x.x networks
        ]

        # Get broadcast addresses from Home Assistant's network helper
        try:
            ha_broadcast_addresses = await async_get_ipv4_broadcast_addresses(hass)
            ha_broadcast_strings = [str(addr) for addr in ha_broadcast_addresses]
            broadcast_addresses.extend(ha_broadcast_strings)
            _LOGGER.debug(f"Found broadcast addresses from HA: {ha_broadcast_strings}")
        except Exception as e:
            _LOGGER.debug(f"Could not get HA broadcast addresses: {e}")

        # Remove duplicates
        broadcast_addresses = list(dict.fromkeys(broadcast_addresses))

        sockets = _build_discovery_sockets(interface_targets)
        for sock, _, ifname in sockets:
            sock.setblocking(False)
            _LOGGER.debug(f"Using discovery socket bound for interface '{ifname}': {sock.getsockname()}")

        # Send discovery through each available interface socket
        for sock, preferred_broadcasts, ifname in sockets:
            target_broadcasts = list(dict.fromkeys(
                preferred_broadcasts
                + [
                    broadcast_addr
                    for broadcast_addr in broadcast_addresses
                    if _broadcast_matches_source(sock.getsockname()[0], broadcast_addr)
                ]
            ))
            for broadcast_addr in target_broadcasts:
                try:
                    _LOGGER.debug(
                        f"Sending discovery to {broadcast_addr} via interface '{ifname}' from {sock.getsockname()[0]}"
                    )
                    sock.sendto(DISCOVERY_MESSAGE, (broadcast_addr, BROADCAST_PORT))
                except Exception as e:
                    _LOGGER.debug(f"Failed to send to {broadcast_addr} via {ifname}: {e}")

        _LOGGER.debug("Sent discovery packets, waiting for replies...")

        start = time.time()
        while time.time() - start < timeout:
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                break

            ready_sockets, _, _ = select.select(
                [sock for sock, _, _ in sockets],
                [],
                [],
                min(remaining, 0.5),
            )
            if not ready_sockets:
                continue

            for sock in ready_sockets:
                ifname = next(interface_name for current_sock, _, interface_name in sockets if current_sock is sock)
                try:
                    data, addr = sock.recvfrom(1024)
                    try:
                        # Try to parse as JSON and decrypt if possible
                        response = simplejson.loads(data.decode(errors="ignore"))
                        if "pack" in response:
                            pack = response["pack"]
                            decoded_pack = base64.b64decode(pack)

                            # Discovery responses typically use level 1 encryption (ECB mode)
                            # But we need to test which encryption the device actually uses for communication
                            pack_json = None

                            try:
                                decrypted_pack = GetGCMCipher(GENERIC_DEVICE_KEY).decrypt(decoded_pack)
                                # Remove null bytes and trailing data after last }
                                decoded_text = decrypted_pack.decode("utf-8", errors="ignore").replace("\x0f", "")
                                last_brace = decoded_text.rfind("}")
                                if last_brace != -1:
                                    clean_text = decoded_text[: last_brace + 1]
                                else:
                                    clean_text = decoded_text
                                pack_json = simplejson.loads(clean_text)
                                _LOGGER.debug(f"Decrypted discovery response from {addr} on interface '{ifname}'")
                            except Exception as e:
                                _LOGGER.debug(f"Could not decrypt discovery response from {addr}: {e}")
                                continue

                            # If we successfully decrypted and got device info
                            if pack_json and pack_json.get("t") == "dev":
                                mac_addr = pack_json.get("mac", "")
                                if not mac_addr:
                                    _LOGGER.debug(f"No MAC address in response from {addr}")
                                    continue

                                # Just collect basic device info for now - encryption detection happens later
                                device_info = {
                                    "name": pack_json.get("name", "") or f"Gree {mac_addr[-4:]}",
                                    "host": addr[0],
                                    "port": BROADCAST_PORT,
                                    "mac": mac_addr,
                                    "brand": pack_json.get("brand", "gree"),
                                    "model": pack_json.get("model", "gree"),
                                    "version": pack_json.get("ver", ""),
                                }
                                device_key = (device_info["host"], device_info["mac"])
                                if device_key not in seen_device_ids:
                                    seen_device_ids.add(device_key)
                                    devices.append(device_info)
                                    _LOGGER.debug(f"Discovered Gree device: {device_info}")
                            else:
                                _LOGGER.debug(f"Invalid or missing device info from {addr}")
                        else:
                            _LOGGER.debug(f"Received response without pack from {addr}: {response}")
                    except Exception as e:
                        _LOGGER.debug(f"Could not parse response from {addr}: {e}")
                except BlockingIOError:
                    continue
    finally:
        for sock, _, _ in sockets:
            with suppress(Exception):
                sock.close()

    _LOGGER.debug(f"Discovery completed, found {len(devices)} devices")
    return devices
