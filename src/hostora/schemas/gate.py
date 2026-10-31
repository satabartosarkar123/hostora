from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from hostora.schemas.common import ApiModel, GateMode


class GateScan(ApiModel):
    device_id: Annotated[str, StringConstraints(min_length=4, max_length=64)]
    seq: Annotated[int, Field(ge=0)]
    pass_id: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    seats: Annotated[int, Field(ge=1, le=20)]
    at: Annotated[int, Field(gt=0)]


class GateScanBatch(ApiModel):
    function_id: str
    scans: Annotated[list[GateScan], Field(min_length=1, max_length=500)]


class GateManifestPass(ApiModel):
    pass_id: str
    display_name: str
    seats: int


class GateManifest(ApiModel):
    event_key: str
    function_id: str
    function_name: str
    gate_mode: GateMode
    public_key: str
    passes: list[GateManifestPass]
