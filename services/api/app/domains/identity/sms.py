"""SMS provider boundary and stable provider error mapping."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    def __init__(self, error_key: str, message: str):
        super().__init__(message)
        self.error_key = error_key
        self.message = message


@dataclass(frozen=True)
class ProviderReceipt:
    provider: str
    external_id: str | None = None
    demo_code: str | None = None


class DemoSmsProvider:
    def __init__(self, *, environment: str, allowlist: set[str]):
        self.environment = environment
        self.allowlist = allowlist

    async def send(self, phone: str, purpose: str, code: str) -> ProviderReceipt:
        if self.environment == "production":
            raise ProviderError("SMS_PROVIDER_UNAVAILABLE", "生产环境未配置短信服务")
        # 开发环境白名单留空 = 任意手机号均可走演示通道；配置了白名单则仅放行名单内号码
        if self.allowlist and phone not in self.allowlist:
            raise ProviderError("SMS_DEMO_PHONE_NOT_ALLOWED", "该手机号不在演示短信白名单")
        return ProviderReceipt(provider="demo", demo_code=code)


def map_tencent_error(vendor_code: str) -> str:
    if "LimitExceeded" in vendor_code:
        return "SMS_RATE_LIMITED"
    if "Signature" in vendor_code or "Template" in vendor_code:
        return "SMS_TEMPLATE_UNAVAILABLE"
    if "Unauthorized" in vendor_code or "AuthFailure" in vendor_code:
        return "SMS_PROVIDER_AUTH_FAILED"
    if "InternalError" in vendor_code or "RequestTimeout" in vendor_code:
        return "SMS_PROVIDER_TEMPORARY_FAILURE"
    return "SMS_PROVIDER_FAILURE"


class TencentSmsProvider:
    """Tencent Cloud SMS SDK boundary with injectable SDK construction."""

    def __init__(
        self,
        *,
        secret_id: str = "",
        secret_key: str = "",
        sdk_app_id: str = "",
        sign_name: str = "",
        template_id: str = "",
        region: str = "ap-guangzhou",
        template_params: list[str] | None = None,
        sender: Callable[[str, str, str], Awaitable[str | None]] | None = None,
        client_factory: Callable[[str, str, str], Any] | None = None,
        request_factory: Callable[[dict[str, object]], Any] | None = None,
    ):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.sdk_app_id = sdk_app_id
        self.sign_name = sign_name
        self.template_id = template_id
        self.region = region
        self.template_params = template_params or ["{code}"]
        self.sender = sender
        self.client_factory = client_factory or self._create_client
        self.request_factory = request_factory or self._create_request

    @staticmethod
    def _create_client(secret_id: str, secret_key: str, region: str) -> Any:
        from tencentcloud.common import credential
        from tencentcloud.sms.v20210111 import sms_client

        return sms_client.SmsClient(credential.Credential(secret_id, secret_key), region)

    @staticmethod
    def _create_request(payload: dict[str, object]) -> Any:
        from tencentcloud.sms.v20210111 import models

        request = models.SendSmsRequest()
        request.from_json_string(json.dumps(payload))
        return request

    def _request_payload(self, phone: str, code: str) -> dict[str, object]:
        return {
            "SmsSdkAppId": self.sdk_app_id,
            "SignName": self.sign_name,
            "TemplateId": self.template_id,
            "PhoneNumberSet": [f"+86{phone}"],
            "TemplateParamSet": [param.replace("{code}", code) for param in self.template_params],
        }

    async def send(self, phone: str, purpose: str, code: str) -> ProviderReceipt:
        try:
            if self.sender is not None:
                external_id = await self.sender(phone, purpose, code)
                return ProviderReceipt(provider="tencent", external_id=external_id)
            request = self.request_factory(self._request_payload(phone, code))
            client = self.client_factory(self.secret_id, self.secret_key, self.region)
            response = await asyncio.to_thread(client.SendSms, request)
        except Exception as exc:
            vendor_code = str(getattr(exc, "code", exc))
            raise ProviderError(map_tencent_error(vendor_code), "短信发送失败") from None
        statuses = getattr(response, "SendStatusSet", None) or []
        status = statuses[0] if statuses else None
        if status is None or getattr(status, "Code", "") != "Ok":
            vendor_code = str(getattr(status, "Code", ""))
            raise ProviderError(map_tencent_error(vendor_code), "短信发送失败")
        return ProviderReceipt(provider="tencent", external_id=getattr(response, "RequestId", None))
