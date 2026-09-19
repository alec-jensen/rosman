import socket
import xml.etree.ElementTree as ET

from rosman.networking import dds_port_range, detect_lan_ip, render_cyclonedds_xml


def test_render_with_no_peers():
    xml = render_cyclonedds_xml([])
    assert "<Peers />" in xml
    assert "AllowMulticast>false" in xml


def test_render_with_peers_includes_each_as_an_element():
    xml = render_cyclonedds_xml(["rosman-a-1234", "rosman-b-5678"])
    assert '<Peer address="rosman-a-1234" />' in xml
    assert '<Peer address="rosman-b-5678" />' in xml


def test_render_produces_well_formed_xml_for_odd_peer_names():
    # Peer names come from Docker container names, which shouldn't contain
    # quotes/special chars in practice -- but the renderer must never emit
    # broken XML regardless, and the peer name must round-trip intact.
    odd_name = 'rosman-"weird"-<name>&'
    xml = render_cyclonedds_xml([odd_name])
    root = ET.fromstring(xml)
    peers = root.findall(".//{https://cdds.io/config}Peer")
    assert [p.get("address") for p in peers] == [odd_name]


def test_render_includes_remote_peers_alongside_local_ones():
    xml = render_cyclonedds_xml(["rosman-a-1234"], ["192.168.1.51"])
    assert '<Peer address="rosman-a-1234" />' in xml
    assert '<Peer address="192.168.1.51" />' in xml


def test_render_with_only_remote_peers():
    xml = render_cyclonedds_xml([], ["192.168.1.51", "192.168.1.52"])
    assert xml.count("<Peer ") == 2


def test_dds_port_range_depends_only_on_domain_id():
    # Two machines running the same checked-in rosman.yml (same domain_id)
    # must derive the identical window with zero coordination -- that's the
    # whole point of mirroring Cyclone's own default port formula instead of
    # negotiating a port some other way.
    assert dds_port_range(0) == dds_port_range(0)
    assert dds_port_range(0) != dds_port_range(1)
    r = dds_port_range(0)
    assert r.start == 7400


def test_dds_port_range_does_not_overlap_across_domains():
    a, b = dds_port_range(0), dds_port_range(1)
    assert set(a).isdisjoint(set(b))


def test_detect_lan_ip_returns_none_on_socket_error(monkeypatch):
    class FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, addr):
            raise OSError("network unreachable")

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FailingSocket())
    assert detect_lan_ip() is None


def test_detect_lan_ip_returns_local_address(monkeypatch):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.50", 12345)

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSocket())
    assert detect_lan_ip() == "192.168.1.50"
