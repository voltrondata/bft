import math
import operator

import cudf
import numpy

from bft.cases.runner import SqlCaseResult, SqlCaseRunner
from bft.cases.types import Case
from bft.dialects.types import SqlMapping
from bft.utils.utils import type_to_dialect_type

type_map = {
    "i8": cudf.dtype("int8"),
    "i16": cudf.dtype("int16"),
    "i32": cudf.dtype("int32"),
    "i64": cudf.dtype("int64"),
    "fp32": cudf.dtype("float32"),
    "fp64": cudf.dtype("float64"),
    "boolean": cudf.dtype("bool"),
    "string": cudf.dtype("string"),
    "timestamp": cudf.dtype("datetime64[s]"),
    "date": cudf.dtype("datetime64[s]"),
}


def type_to_cudf_dtype(type: str):
    return type_to_dialect_type(type, type_map)


def is_string_function(data_types):
    return cudf.dtype("string") in data_types


def is_datetime_function(data_types):
    return cudf.dtype("datetime64[s]") in data_types


def is_numpy_type(data_type):
    return type(data_type).__module__ == numpy.__name__


def get_str_fn_result(
    fn_name: str, arg_vectors: list[cudf.Series], arg_values: list[str], is_regexp: bool
):
    if len(arg_vectors) == 1:
        fn = getattr(arg_vectors[0].str, fn_name)
        return fn()
    elif len(arg_vectors) == 2:
        fn = getattr(arg_vectors[0].str, fn_name)
        if is_regexp:
            return fn(arg_values[1], regex=True)
        else:
            return fn(arg_values[1])
    else:
        fn = getattr(arg_vectors[0].str, fn_name)
        opt_arg = True if arg_values[2] is not None else False
        if opt_arg and is_regexp:
            return fn(arg_values[1], arg_values[2], regex=True)
        elif opt_arg:
            return fn(arg_values[1], arg_values[2])
        else:
            return fn(arg_values[1])


def get_dt_fn_result(
    mapping: str, dtype, arg_vectors: list[cudf.Series], arg_values: list[str]
):
    fn_name = mapping.local_name
    if len(arg_vectors) == 2:
        if mapping.infix:
            gdf = cudf.DataFrame(
                {"a": arg_values[0], "b": arg_values[1]},
                dtype=dtype,
            )
            result = gdf.eval(f"(a){fn_name}(b)")
        elif mapping.extract:
            extract_property = arg_values[0].lower()
            result = getattr(arg_vectors[1].dt, extract_property)
    return result


class CudfRunner(SqlCaseRunner):
    def __init__(self, dialect):
        super().__init__(dialect)

    def run_sql_case(self, case: Case, mapping: SqlMapping) -> SqlCaseResult:
        arg_vectors = []
        arg_values = []
        data_types = []
        fn_name = mapping.local_name
        is_regexp = True if "regexp" in case.function else False
        for arg in case.args:
            dtype = type_to_cudf_dtype(arg.type)
            if dtype is None:
                return SqlCaseResult.unsupported(
                    f"The type {arg.type} is not supported"
                )
            arg_vectors.append(cudf.Series(arg.value, dtype=dtype))
            arg_values.append(arg.value)
            data_types.append(dtype)

        try:
            if is_datetime_function(data_types):
                result = get_dt_fn_result(mapping, dtype, arg_vectors, arg_values)
            elif is_string_function(data_types):
                result = get_str_fn_result(fn_name, arg_vectors, arg_values, is_regexp)
            elif len(arg_vectors) == 1:
                # Some functions that only take a single arg are able to be executed against
                # both a Series and a Dataframe whereas others are only able to be executed against a Dataframe.
                if mapping.aggregate:
                    arg_values = arg_values[0]
                try:
                    gdf = cudf.DataFrame({"a": arg_values}, dtype=dtype)
                    result = gdf.eval(f"{fn_name}(a)")
                except ValueError:
                    fn = getattr(arg_vectors[0], fn_name)
                    result = fn()
            elif len(arg_vectors) == 2:
                if mapping.infix:
                    # If there are only Null/Nan/None values in the column, they are set to False instead of <NA>.
                    # We add extra data to ensure the <NA> value exists in the dataframe.
                    gdf = cudf.DataFrame(
                        {"a": [arg_values[0], True], "b": [arg_values[1], True]},
                        dtype=dtype,
                    )
                    result = gdf.eval(f"(a){fn_name}(b)")
                else:
                    try:
                        fn = getattr(arg_vectors[0], fn_name)
                        result = fn(arg_vectors[1])
                    except AttributeError:
                        fn = getattr(operator, fn_name)
                        result = fn(arg_vectors[0], arg_vectors[1])
                    except ValueError:  # Case for round function
                        fn = getattr(arg_vectors[0], fn_name)
                        result = fn(arg_values[1])
            else:
                fn = getattr(arg_vectors[0], fn_name)
                try:
                    result = fn(arg_vectors[1:])
                except TypeError:
                    result = fn(arg_values[1], arg_values[2])
        except RuntimeError as err:
            return SqlCaseResult.error(str(err))

        if mapping.aggregate:
            if is_numpy_type(result):
                result = result.item()
        else:
            if result.empty and (
                case.result.value is None or case.result.value is False
            ):
                return SqlCaseResult.success()
            elif len(result) != 1 and not mapping.infix:
                raise Exception("Scalar function with one row output more than one row")
            else:
                result = result[0]

        if case.result == "undefined":
            return SqlCaseResult.success()
        elif case.result == "error":
            return SqlCaseResult.unexpected_pass(str(result))
        elif case.result == "nan":
            if math.isnan(result):
                return SqlCaseResult.success()
        else:
            if case.result.value is None:
                if str(result) == "<NA>" or math.isnan(result) or result is None:
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
            elif case.result.value == result:
                return SqlCaseResult.success()
            elif case.result.value == str(result):
                return SqlCaseResult.success()
            elif numpy.float32(case.result.value) == result:
                return SqlCaseResult.success()
            else:
                return SqlCaseResult.mismatch(str(result))
