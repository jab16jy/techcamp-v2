from techcamp.identity.adapters.security.otp_store import OtpStore


def test_issued_code_verifies_once() -> None:
    store = OtpStore()
    code = store.issue("+573000000005")

    assert store.verify("+573000000005", code) is True
    assert store.verify("+573000000005", code) is False


def test_wrong_code_is_rejected() -> None:
    store = OtpStore()
    store.issue("+573000000006")

    assert store.verify("+573000000006", "000000") is False


def test_unknown_phone_is_rejected() -> None:
    store = OtpStore()

    assert store.verify("+573000000007", "123456") is False
