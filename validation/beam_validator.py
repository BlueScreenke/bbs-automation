from constants.rebar_table import REBAR_DIAMETERS
from validation.common import ValidationError
from validation.bar_validator import validate_bar

def validate_beam(beam):
    errors = []

    if not beam.id:
        errors.append(
            ValidationError("Beam", "Missing beam ID")
        )

    if not beam.bars:
        errors.append(
            ValidationError(
                f"Beam {beam.id}",
                "Beam has no reinforcement bars"
            )
        )

    for bar in beam.bars:
        bar_errors = validate_bar(bar)
        errors.extend(bar_errors)

    return errors