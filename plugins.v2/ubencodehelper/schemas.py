from typing import Literal

from pydantic import BaseModel, Field


class BindRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    captcha_id: str = Field(min_length=1, max_length=256)
    captcha_answer: str = Field(min_length=1, max_length=32)


class DownloaderCheckRequest(BaseModel):
    downloader: str = Field(min_length=1, max_length=128)


class ConfigCheckRequest(DownloaderCheckRequest):
    cron: str = Field(min_length=1, max_length=128)


class TaskActionRequest(BaseModel):
    task_id: int = Field(gt=0)
    action: Literal[
        "claim_test",
        "claim_encode",
        "claim_test_download",
        "claim_encode_download",
        "push_source",
        "show_result",
        "cancel",
    ]
