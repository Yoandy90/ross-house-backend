from rental import store_routing as r


def test_decode_google_polyline():
    points = r._decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq\`@")
    assert points == [
        {"latitude": 38.5, "longitude": -120.2},
        {"latitude": 40.7, "longitude": -120.95},
        {"latitude": 43.252, "longitude": -126.453},
    ]


def test_invalid_polyline_is_safe():
    assert r._decode_polyline("~") == []


def test_duration_parser():
    assert r._seconds("123.4s") == 123
    assert r._seconds("-1s") is None
    assert r._seconds("bad") is None
