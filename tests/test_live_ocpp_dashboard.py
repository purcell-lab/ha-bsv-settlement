import copy
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location("ocpp_dashboard",Path(__file__).parents[1]/"frontend/ocpp_dashboard.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_readonly_native_panels_preserve_existing_cards_and_are_idempotent():
    keys=["observer","connector","transaction","power","current","voltage","soc","error",
          "session_energy","export_energy","import_register","meter_start","duration"]
    entities={key:"sensor.fictional_"+key for key in keys}
    config={"views":[{"path":"overview","sections":[{"cards":[
        {"type":"tile","entity":entities["observer"]},{"type":"markdown","content":"Preserve me"}]}]},
        {"path":"other","cards":[{"type":"unchanged"}]}]}
    before=copy.deepcopy(config)
    updated=module.add_live_ocpp(config,entities)
    assert config==before and updated["views"][1]==before["views"][1]
    assert module.add_live_ocpp(updated,entities)==updated
    cards=updated["views"][0]["sections"][0]["cards"]
    assert cards[:2]==before["views"][0]["sections"][0]["cards"]
    assert "Estimated export" in cards[3]["entities"][1]["name"]
    assert cards[2]["show_header_toggle"] is False
    assert not any("service" in str(c) or "toggle" == c.get("tap_action",{}).get("action") for c in cards)
    with pytest.raises(ValueError):
        module.add_live_ocpp(config,entities|{"power":"switch.unsafe"})
