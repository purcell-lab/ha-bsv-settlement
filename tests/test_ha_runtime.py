"""Optional tests against the real Home Assistant Python package, not HA mocks.

No wallet, network or funds. Install homeassistant + pytest-asyncio to run.
"""
import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow


@pytest.mark.asyncio
async def test_real_config_flow_form(tmp_path):
    hass = HomeAssistant(str(tmp_path / "ha"))
    try:
        flow = BSVSettlementConfigFlow()
        flow.hass = hass
        flow.context = {}
        result = await flow.async_step_user()
        assert result["type"] == "form"
        assert result["step_id"] == "user"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_config_flow_offers_no_mock_or_testnet_and_never_defaults_to_mainnet(tmp_path):
    from test_embedded import make_hass
    hass = await make_hass(tmp_path)
    try:
        flow = BSVSettlementConfigFlow()
        flow.hass = hass
        flow.context = {}
        schema = (await flow.async_step_user())["data_schema"].schema
        key = next(iter(schema))
        assert key.default() == "sensor_proxy"
        assert set(schema[key].container) == {"sensor_proxy", "ocpp_import_shadow", "embedded_mainnet"}
        assert not hasattr(flow, "async_step_mock") and not hasattr(flow, "async_step_embedded")
        for removed in ("mock", "embedded_testnet"):
            result = await flow.async_step_user({"backend": removed})
            assert result["type"] == "form" and result["step_id"] == "user"
        assert (await flow.async_step_user({"backend": "embedded_mainnet"}))["step_id"] == "mainnet"
        acks = {"acknowledge_key_custody": True, "acknowledge_mainnet": True, "enable_broadcast": True}
        for missing in acks:
            refused = await flow.async_step_mainnet({**acks, missing: False})
            assert refused["type"] == "form"
            assert refused["errors"]["base"] == "acknowledgement_required"
        result = await flow.async_step_mainnet(acks)
        assert result["type"] == "create_entry"
        assert result["data"] == {"backend": "embedded_mainnet", "network": "mainnet", **acks}
        assert not any("secret" in key for key in result["data"])
    finally:
        await hass.async_stop(force=True)
