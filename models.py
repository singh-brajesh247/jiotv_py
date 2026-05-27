"""Typed models used by the converted Python application."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, TypedDict


class JSONDict(TypedDict, total=False):
    code: int
    message: str
    result: Any


@dataclass(slots=True)
class JioTVCredentials:
    sso_token: str = ""
    unique_id: str = ""
    crm: str = ""
    access_token: str = ""
    refresh_token: str = ""
    last_token_refresh_time: str = ""
    last_sso_token_refresh_time: str = ""

    def to_store(self) -> dict[str, str]:
        return {
            "ssoToken": self.sso_token,
            "uniqueId": self.unique_id,
            "crm": self.crm,
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "lastTokenRefreshTime": self.last_token_refresh_time,
            "lastSSOTokenRefreshTime": self.last_sso_token_refresh_time,
        }


@dataclass(slots=True)
class Channel:
    id: str
    name: str
    url: str = ""
    logo_url: str = ""
    category: int = 0
    language: int = 0
    is_hd: bool = False
    is_catchup_available: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Channel":
        return cls(
            id=str(data.get("channel_id", data.get("id", ""))),
            name=str(data.get("channel_name", data.get("name", ""))),
            url=str(data.get("channel_url", data.get("url", ""))),
            logo_url=str(data.get("logoUrl", data.get("logo_url", ""))),
            category=int(data.get("channelCategoryId", data.get("category", 0)) or 0),
            language=int(data.get("channelLanguageId", data.get("language", 0)) or 0),
            is_hd=bool(data.get("isHD", data.get("is_hd", False))),
            is_catchup_available=bool(data.get("isCatchupAvailable", False)),
        )

    def to_api(self) -> dict[str, Any]:
        return {
            "channel_id": self.id,
            "channel_name": self.name,
            "channel_url": self.url,
            "logoUrl": self.logo_url,
            "channelCategoryId": self.category,
            "channelLanguageId": self.language,
            "isHD": self.is_hd,
            "isCatchupAvailable": self.is_catchup_available,
        }


@dataclass(slots=True)
class ChannelsResponse:
    code: int = 0
    message: str = ""
    result: list[Channel] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "ChannelsResponse":
        channels = [Channel.from_api(item) for item in data.get("result", [])]
        return cls(
            code=int(data.get("code", 0) or 0),
            message=str(data.get("message", "")),
            result=channels,
        )

    def to_api(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "result": [channel.to_api() for channel in self.result],
        }


@dataclass(slots=True)
class Bitrates:
    auto: str = ""
    high: str = ""
    low: str = ""
    medium: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> "Bitrates":
        data = data or {}
        return cls(
            auto=str(data.get("auto", "") or ""),
            high=str(data.get("high", "") or ""),
            low=str(data.get("low", "") or ""),
            medium=str(data.get("medium", "") or ""),
        )


@dataclass(slots=True)
class MPD:
    result: str = ""
    key: str = ""
    bitrates: Bitrates = field(default_factory=Bitrates)

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> "MPD":
        data = data or {}
        return cls(
            result=str(data.get("result", "") or ""),
            key=str(data.get("key", "") or ""),
            bitrates=Bitrates.from_api(data.get("bitrates")),
        )


@dataclass(slots=True)
class LiveURLOutput:
    bitrates: Bitrates = field(default_factory=Bitrates)
    code: int = 0
    content_id: float = 0.0
    current_time: float = 0.0
    end_time: float = 0.0
    message: str = ""
    result: str = ""
    start_time: float = 0.0
    vod_stitch: bool = False
    mpd: MPD = field(default_factory=MPD)
    is_drm: bool = False
    ext_id: str = ""
    algo_name: str = ""
    hdnea: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "LiveURLOutput":
        return cls(
            bitrates=Bitrates.from_api(data.get("bitrates")),
            code=int(data.get("code", 0) or 0),
            content_id=float(data.get("contentId", 0) or 0),
            current_time=float(data.get("currentTime", 0) or 0),
            end_time=float(data.get("endTime", 0) or 0),
            message=str(data.get("message", "") or ""),
            result=str(data.get("result", "") or ""),
            start_time=float(data.get("startTime", 0) or 0),
            vod_stitch=bool(data.get("vodStitch", False)),
            mpd=MPD.from_api(data.get("mpd")),
            is_drm=bool(data.get("isDRM", False)),
            ext_id=str(data.get("extId", "") or ""),
            algo_name=str(data.get("algoName", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
