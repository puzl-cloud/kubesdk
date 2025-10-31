import unittest
from kubesdk.common import host_from_url


class TestHostFromUrl100(unittest.TestCase):
    # ---- early returns / input sanitation ----
    def test_none_and_empty(self):
        self.assertIsNone(host_from_url(None))
        self.assertIsNone(host_from_url(""))
        self.assertIsNone(host_from_url("   "))

    # ---- clearly not-a-host cases ----
    def test_paths_and_special_schemes(self):
        self.assertIsNone(host_from_url("/just/a/path"))
        self.assertIsNone(host_from_url("mailto:user@example.com"))
        self.assertIsNone(host_from_url("file:///etc/hosts"))

    # ---- regular scheme cases (no port) ----
    def test_with_scheme_no_port(self):
        self.assertEqual(host_from_url("http://example.com/path?x=1"), "example.com")
        self.assertEqual(host_from_url("https://Sub.ExAmPlE.com/"), "sub.example.com")
        self.assertEqual(host_from_url("ftp://example.com/resource"), "example.com")

    # ---- regular scheme cases (with port) + include_port toggle ----
    def test_with_scheme_and_port(self):
        self.assertEqual(host_from_url("http://example.com:8080/path"), "example.com:8080")
        self.assertEqual(host_from_url("HTTP://EXAMPLE.COM:80"), "example.com:80")
        # include_port=False must drop the port
        self.assertEqual(host_from_url("http://example.com:8080/aaa", include_port=False), "example.com")

    # ---- schemeless domain (no port) ----
    def test_schemeless_no_port(self):
        self.assertEqual(host_from_url("example.com/path"), "example.com")
        # IPv4 without scheme
        self.assertEqual(host_from_url("1.2.3.4/zzz"), "1.2.3.4")

    # ---- schemeless host:port and host:port/extra (the added behavior) ----
    def test_schemeless_with_port_and_path(self):
        self.assertEqual(host_from_url("example.com:9090/path"), "example.com:9090")
        self.assertEqual(host_from_url("example.com:9090/path", include_port=False), "example.com")
        self.assertEqual(host_from_url("LOCALHOST:3000/api/v1"), "localhost:3000")

    # ---- IPv6 cases ----
    def test_ipv6(self):
        # With scheme and port: returned host must be bracketed when include_port=True
        self.assertEqual(host_from_url("http://[2001:db8::1]:8080/foo"), "[2001:db8::1]:8080")
        # include_port=False: raw hostname (no brackets)
        self.assertEqual(host_from_url("http://[2001:db8::1]:8080/foo", include_port=False), "2001:db8::1")

        # Schemeless IPv6 with port (already bracketed in input)
        self.assertEqual(host_from_url("[2001:db8::2]:9090"), "[2001:db8::2]:9090")

        # Schemeless IPv6 without port (brackets in input, function strips them)
        self.assertEqual(host_from_url("[2001:db8::3]"), "2001:db8::3")

    # ---- whitespace trimming ----
    def test_whitespace_trim(self):
        self.assertEqual(host_from_url("  https://example.com  "), "example.com")
        self.assertEqual(host_from_url("  example.com:4444  "), "example.com:4444")
