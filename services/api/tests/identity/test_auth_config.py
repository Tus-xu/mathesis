import pytest

from app.config import Settings


def test_production_rejects_documented_placeholder_peppers():
    config = Settings(
        app_env="production",
        jwt_secret="a-production-secret-that-is-long-enough",
        auth_sms_provider="tencent",
        auth_otp_pepper="replace-with-random-secret",
        auth_refresh_token_pepper="replace-with-different-random-secret",
        auth_invite_pepper="replace-with-third-random-secret",
    )

    with pytest.raises(RuntimeError, match="AUTH_OTP_PEPPER"):
        config.validate_production()


@pytest.mark.parametrize(
    "template_params",
    [
        "not-json",
        '["{code}", 5]',
        '["not-a-code-placeholder"]',
    ],
)
def test_production_rejects_invalid_tencent_template_parameter_configuration(template_params):
    config = Settings(
        app_env="production",
        jwt_secret="a-production-secret-that-is-long-enough",
        auth_sms_provider="tencent",
        auth_otp_pepper="a" * 32,
        auth_refresh_token_pepper="b" * 32,
        auth_invite_pepper="c" * 32,
        tencent_sms_secret_id="secret-id",
        tencent_sms_secret_key="secret-key",
        tencent_sms_sdk_app_id="1400000000",
        tencent_sms_sign_name="数学竞技场",
        tencent_sms_template_id="1000000",
        tencent_sms_template_params=template_params,
    )

    with pytest.raises(RuntimeError, match="TENCENT_SMS_TEMPLATE_PARAMS"):
        config.validate_production()


@pytest.mark.parametrize(
    ("overrides", "expected_setting"),
    [
        ({"auth_sms_provider": "unsupported"}, "AUTH_SMS_PROVIDER"),
        ({"tencent_sms_secret_id": ""}, "TENCENT_SMS_SECRET_ID"),
        ({"tencent_sms_region": ""}, "TENCENT_SMS_REGION"),
        ({"tencent_sms_template_params": "[]"}, "TENCENT_SMS_TEMPLATE_PARAMS"),
    ],
)
def test_production_rejects_incomplete_or_unsupported_sms_provider_configuration(
    overrides, expected_setting
):
    values = {
        "app_env": "production",
        "jwt_secret": "a-production-secret-that-is-long-enough",
        "auth_sms_provider": "tencent",
        "auth_otp_pepper": "a" * 32,
        "auth_refresh_token_pepper": "b" * 32,
        "auth_invite_pepper": "c" * 32,
        "tencent_sms_secret_id": "secret-id",
        "tencent_sms_secret_key": "secret-key",
        "tencent_sms_sdk_app_id": "1400000000",
        "tencent_sms_sign_name": "数学竞技场",
        "tencent_sms_template_id": "1000000",
        "tencent_sms_region": "ap-guangzhou",
        "tencent_sms_template_params": '["{code}"]',
    }
    values.update(overrides)

    with pytest.raises(RuntimeError, match=expected_setting):
        Settings(**values).validate_production()
