from pydantic import BaseModel
from typing import Optional, List


class LoginRequest(BaseModel):
    username: str
    password: str


class OperatorSelectRequest(BaseModel):
    user_id: int


# ── PRODUCTION ─────────────────────────────────────────────────────────────

class ItemProduceRequest(BaseModel):
    """Unified production endpoint for ALL stations.
    Creates a NEW labeled output item for the given station.
    """
    lot_id:              Optional[int]  = None
    sales_order_line_id: int
    station:             str            # where this output is being produced
    next_stage:          str            # destination
    gross_weight:        float
    machine_id:          int
    # Label options
    branding_mode:       str            = "normal"   # normal | distributor
    print_weight_mode:   str            = "gross"    # gross | net
    # Tare override
    tare_rule_id:        Optional[int]  = None
    bobbin_weight_kg:    Optional[float] = None      # manual tare override
    # Replacement link
    replaces_item_code:  Optional[str]  = None
    # Optional input traceability
    source_item_codes:   List[str]      = []


class ItemCancelRequest(BaseModel):
    reason: str
    notes:  Optional[str] = None


# ── LABELS ─────────────────────────────────────────────────────────────────

class LabelPrintRequest(BaseModel):
    reason:           str = "print"
    branding_mode:    Optional[str] = None   # override; uses item value if None
    print_weight_mode: Optional[str] = None  # override; uses item value if None


class LabelReprintRequest(BaseModel):
    reason: str = "Reimpresión desde operador"


# ── USERS / ADMIN ──────────────────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    username:  str
    password:  str
    full_name: str
    role:      str   # operator | admin | supervisor


class UserUpdateStatusRequest(BaseModel):
    is_active: bool


class UserResetPasswordRequest(BaseModel):
    new_password: str


class CorrectionCreateRequest(BaseModel):
    target_event_id: int
    field_name:      str
    new_value:       str
    reason:          str


# ── PRODUCTS / CONFIG ──────────────────────────────────────────────────────

class ProductCreateRequest(BaseModel):
    description:       str
    tare_constant_kg:  float = 10.0
    tolerance_percent: float = 5.0


class ProductUpdateRequest(BaseModel):
    tare_constant_kg:  float
    tolerance_percent: float


class MachineCreateRequest(BaseModel):
    station_id:   int
    machine_code: str
    machine_name: str


class MachineUpdateRequest(BaseModel):
    machine_name: Optional[str] = None
    station_id:   Optional[int] = None
    is_active:    Optional[bool] = None


class SalesOrderLineCreateRequest(BaseModel):
    product_description:       str
    presentation_type:         Optional[str]   = None
    measure:                   Optional[str]   = None
    caliber:                   Optional[str]   = None
    color:                     Optional[str]   = None
    ordered_kg:                float
    default_branding_mode:     str             = "normal"
    default_print_weight_mode: str             = "gross"
    default_tare_rule_id:      Optional[int]   = None
    bobbin_weight_kg:          Optional[float] = None
    require_bobbin_tare:       bool            = False


# ── TARE RULES ─────────────────────────────────────────────────────────────

class TareRuleCreateRequest(BaseModel):
    name:                    str
    rule_type:               str             = "fixed"  # fixed | linear_density | threshold | manual
    fixed_weight_kg:         Optional[float] = None
    linear_density_kg_per_m: Optional[float] = None
    default_length_m:        Optional[float] = None
    threshold_field:         Optional[str]   = None
    threshold_min:           Optional[float] = None
    threshold_max:           Optional[float] = None
    product_code:            Optional[str]   = None
    station_code:            Optional[str]   = None
    is_default:              bool            = False
    notes:                   Optional[str]   = None


class TareRuleUpdateRequest(BaseModel):
    name:                    Optional[str]   = None
    rule_type:               Optional[str]   = None
    fixed_weight_kg:         Optional[float] = None
    linear_density_kg_per_m: Optional[float] = None
    default_length_m:        Optional[float] = None
    is_default:              Optional[bool]  = None
    is_active:               Optional[bool]  = None
    notes:                   Optional[str]   = None


# ── PALLETS ────────────────────────────────────────────────────────────────

class PalletCreateRequest(BaseModel):
    sales_order_id:      int
    sales_order_line_id: Optional[int] = None


class PalletAddItemRequest(BaseModel):
    item_code: str


# ── CUSTOMERS ──────────────────────────────────────────────────────────────

class CustomerBrandingUpdateRequest(BaseModel):
    default_branding_mode: str   # 'normal' | 'distributor'
