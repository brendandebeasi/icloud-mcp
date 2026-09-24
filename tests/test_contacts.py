import vobject

from icloud_mcp import contacts

VCARD = """BEGIN:VCARD
VERSION:3.0
FN:Jane Doe
N:Doe;Jane;;;
TEL;TYPE=HOME,VOICE:+1 (555) 010-1234
TEL;TYPE=WORK:+1 555 010 9999
EMAIL;TYPE=INTERNET,HOME:jane@example.com
NOTE:Met at <b>conf</b><br>2025
END:VCARD"""


def test_replace_entries_preserves_labels():
    vcard = vobject.readOne(VCARD)
    contacts._apply_fields(vcard, ["+15550101234", "+1 555 000 0000"], None, None, None, None)
    tels = {t.value: t.params for t in vcard.tel_list}
    assert tels["+15550101234"]["TYPE"] == ["HOME", "VOICE"]  # same number, label kept
    assert tels["+1 555 000 0000"]["TYPE"] == ["CELL"]  # new number, default label
    assert len(vcard.tel_list) == 2


def test_apply_notes_and_read_back(monkeypatch):
    monkeypatch.setattr(contacts.config, "HTML_MODE", "text")
    vcard = vobject.readOne(VCARD)
    data = contacts._vcard_to_dict(vcard, "https://p1-contacts.icloud.com/c.vcf")
    assert data["notes"] == "Met at conf\n2025"
    assert data["phones"] == ["+1 (555) 010-1234", "+1 555 010 9999"]
    contacts._apply_fields(vcard, None, None, None, None, None, notes="plain")
    assert vcard.note.value == "plain"
    contacts._apply_fields(vcard, None, None, None, None, None, notes="")
    assert vcard.note.value == ""
