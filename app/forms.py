from wtforms import DecimalField
from wtforms.validators import NumberRange

# Les montants sont stockés en Numeric(10, 2).
MAX_AMOUNT = 99_999_999.99


def amount_range():
    return NumberRange(min=0, max=MAX_AMOUNT, message="Montant entre 0 et 99 999 999,99 €.")


class FrDecimalField(DecimalField):
    """Montant saisi à la française : « 1 200,50 » ou « 1200.50 »."""

    def process_formdata(self, valuelist):
        if valuelist:
            valuelist = [v.replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
                         for v in valuelist]
        super().process_formdata(valuelist)
        if self.data is not None and not self.data.is_finite():
            self.data = None
            raise ValueError("Montant invalide.")
