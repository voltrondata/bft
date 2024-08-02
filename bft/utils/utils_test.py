from decimal import Decimal

from bft.utils.utils import compareDecimalResult


def test_compare_decimal_result():
    assert compareDecimalResult(Decimal('1'), Decimal('1'))
    assert compareDecimalResult(Decimal('99999999999999999999999999999999999999'), Decimal('99999999999999999999999999999999999999'))
    assert compareDecimalResult(Decimal('1.75'), Decimal('1.75678'))
    assert compareDecimalResult(Decimal('1.757'), Decimal('1.75678')) == False
    assert compareDecimalResult(Decimal('2.33'), Decimal('2.330000000078644688'))
    assert compareDecimalResult(Decimal('4.12500053644180'), Decimal('4.1250005364418029785156'))
