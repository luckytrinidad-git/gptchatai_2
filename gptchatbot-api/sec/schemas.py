from typing import Optional, List, Dict, Union

from ninja import Schema, ModelSchema

class LogInput(Schema):
    username: str = None
    action: str = None
    module: str = None
    status: str = None

    class Config:
        extra = 'forbid'
