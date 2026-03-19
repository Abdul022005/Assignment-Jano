from enum import Enum


class MedicationSource(str, Enum):
    CLINIC_EMR = "clinic_emr"
    HOSPITAL_DISCHARGE = "hospital_discharge"
    PATIENT_REPORTED = "patient_reported"


class MedicationStatus(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"


class ConflictType(str, Enum):
    DOSE_MISMATCH = "dose_mismatch"           # same drug, different dose across sources
    STOPPED_VS_ACTIVE = "stopped_vs_active"   # drug stopped in one source, active in another
    CLASS_CONFLICT = "class_conflict"         # two drugs from a blacklisted combination


class ConflictStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"