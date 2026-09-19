import xml.etree.ElementTree as ET

from rosman.networking import render_cyclonedds_xml


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
