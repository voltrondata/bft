from decimal import Decimal, ROUND_DOWN, getcontext, localcontext
from typing import Dict


def type_to_dialect_type(type: str, type_map: Dict[str, str])->str:
    """
    Convert a substrait type to a dialect type

    :param type: substrait name of base type (i.e. without parameters)
    :param type_map:map of substrait type to dialect base type (i.e. without parameters)
    :return:dialect type

    e.g. type_map: {"interval": "INTERVAL", "decimal": "NUMERIC"}
        input type: "decimal<37, 3>",  -> output: "NUMERIC(37, 3)"
    e.g. input type: "interval", output: "INTERVAL"

    in above example "decimal" or "interval" are referred as base type whereas decimal<37, 3> is parameterized type

    """
    type_to_check = type.split("<")[0].strip() if "<" in type else type
    if type_to_check not in type_map:
        raise Exception(f"Unrecognized type: {type}")
    type_val = type_map[type_to_check]
    if not "<" in type:
        return type_val
    # transform parameterized type name to have dialect type
    return type.replace(type_to_check, type_val).replace("<", "(").replace(">", ")")

def compareDecimalResult(expected_result: Decimal, actual_result: Decimal)->bool:
    '''
    Compares non-null decimal type based on scale of expected_result
    :param expected_result: expected result. Its scale is considered to be the scale to compare
    :param actual_result:
    :return: bool
    '''
    # make scale of actual_result to same as expected_result for comparison
    scale = abs(expected_result.as_tuple().exponent)
    rounding_format = Decimal(f"1.{'0' * scale}")
    try:
        # set thread precison to 38 since database decimal support max 38
        with localcontext(prec=38) as ctx:
            rounded_result = actual_result.quantize(rounding_format, rounding=ROUND_DOWN)
    except Exception as e:
        print(f"Exception while rounding: {e}")
        return False
    return rounded_result == expected_result
