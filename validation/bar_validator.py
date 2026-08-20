from constants.rebar_table import REBAR_DIAMETERS
from validation.common import ValidationError


def validate_bar(bar):
    errors = []

    if not bar.mark:
        errors.append(
            ValidationError("Bar", "Missing bar mark")
        )

    if bar.diameter not in REBAR_DIAMETERS:
        errors.append(
            ValidationError(
                f"Bar {bar.mark}",
                f"Invalid diameter: {bar.diameter}mm"
            )
        )

    # Guard against None before numeric comparison —
    # length=None means geometric extraction failed; it should have been
    # caught in beam_converter but is handled here as a safety net.
    if bar.length is None:
        errors.append(
            ValidationError(
                f"Bar {bar.mark}",
                "Length is None — bar was not filtered by beam_converter"
            )
        )
    elif bar.length <= 0:
        errors.append(
            ValidationError(
                f"Bar {bar.mark}",
                "Length must be greater than zero"
            )
        )

    if bar.quantity <= 0:
        errors.append(
            ValidationError(
                f"Bar {bar.mark}",
                "Quantity must be greater than zero"
            )
        )

    return errors