import math
from datetime import datetime

import datafusion
import numpy
import pyarrow as pa

from bft.cases.runner import SqlCaseResult, SqlCaseRunner
from bft.cases.types import Case, CaseLiteral
from bft.dialects.types import SqlMapping
from bft.utils.utils import type_to_dialect_type

type_map = {
    "i8": pa.int8(),
    "i16": pa.int16(),
    "i32": pa.int32(),
    "i64": pa.int64(),
    "fp32": pa.float32(),
    "fp64": pa.float64(),
    "boolean": pa.bool_(),
    "string": pa.string(),
    "date": pa.timestamp("s"),
    "time": pa.timestamp("s"),
    "timestamp": pa.timestamp("s"),
    "timestamp_tz": pa.timestamp("s"),
}


def type_to_datafusion_type(type: str):
    return type_to_dialect_type(type, type_map)


def handle_special_cases(lit: CaseLiteral):
    if lit == "nan":
        return math.nan
    elif lit == "inf":
        return float("inf")
    elif lit == "-inf":
        return float("-inf")
    return lit


def is_string_type(arg):
    return (
        arg.type in ["string", "timestamp", "timestamp_tz", "date", "time"]
        or arg.value in ["Null"]
    ) and arg.value is not None


def arg_with_type(arg):
    if is_string_type(arg):
        arg_val = str(arg.value)
    elif isinstance(arg.value, list) or arg.value is None:
        arg_val = None
    elif arg.type.startswith("i"):
        arg_val = int(arg.value)
    elif arg.type.startswith("fp"):
        arg_val = float(arg.value)
    else:
        arg_val = arg.value
    return arg_val


def str_to_datetime(str_val, type):
    if type == "time":
        return datetime.strptime(str_val, "%H:%M:%S.%f")
    if len(str_val) > 19:
        return datetime.strptime(str_val, "%Y-%m-%d %H:%M:%S %Z")
    elif len(str_val) < 16:
        return datetime.strptime(str_val, "%Y-%m-%d")
    else:
        return datetime.strptime(str_val, "%Y-%m-%d %H:%M:%S")


class DatafusionRunner(SqlCaseRunner):
    def __init__(self, dialect):
        super().__init__(dialect)
        self.ctx = datafusion.SessionContext()

    def run_sql_case(self, case: Case, mapping: SqlMapping) -> SqlCaseResult:

        try:
            arg_vectors = []
            arg_names = []
            arg_vals_list = []
            orig_types = []
            arg_types_list = []

            if mapping.aggregate:
                arg_vectors = []
                for arg_idx, arg in enumerate(case.args):
                    arg_vals = []
                    arg_type = type_to_datafusion_type(arg.type)
                    if arg_type is None:
                        return SqlCaseResult.unsupported(f"Unsupported type {arg.type}")
                    for val in arg.value:
                        arg_vals.append(handle_special_cases(val))
                    arg_names.append(f"arg{arg_idx}")
                    arg_vectors.append(pa.array(arg_vals, arg_type))
            else:
                for arg_idx, arg in enumerate(case.args):
                    arg_val = arg_with_type(arg)
                    arg_type = type_to_datafusion_type(arg.type)
                    if arg_type is None:
                        return SqlCaseResult.unsupported(f"Unsupported type {arg.type}")
                    orig_types.append(arg.type)
                    arg_vals_list.append(arg_val)
                    arg_types_list.append(arg_type)
                    arg_names.append(f"arg{arg_idx}")

                for val, arg_type, orig_type in zip(
                    arg_vals_list, arg_types_list, orig_types
                ):
                    if isinstance(arg_type, pa.lib.TimestampType):
                        val = str_to_datetime(val, orig_type)
                    arg_vectors.append(pa.array([val], arg_type))

            joined_arg_names = ",".join(arg_names)
            batch = pa.RecordBatch.from_arrays(
                arg_vectors,
                names=arg_names,
            )
            self.ctx.register_record_batches("my_table", [[batch]])
            if mapping.infix:
                if len(case.args) != 2:
                    raise Exception(f"Infix function with {len(case.args)} args")
                expr_str = f"SELECT {arg_names[0]} {mapping.local_name} {arg_names[1]} FROM my_table;"
            elif mapping.postfix:
                if len(arg_names) != 1:
                    raise Exception(f"Postfix function with {len(arg_names)} args")
                expr_str = f"SELECT {arg_names[0]} {mapping.local_name} FROM my_table;"
            elif mapping.extract:
                if len(arg_names) != 2:
                    raise Exception(f"Extract function with {len(arg_names)} args")
                expr_str = f"SELECT {mapping.local_name}({arg_vals_list[0]} FROM {arg_names[1]}) FROM my_table;"
            elif mapping.local_name == 'count(*)':
                expr_str = f"SELECT {mapping.local_name} FROM my_table;"
            elif mapping.aggregate:
                if len(arg_names) < 1:
                    raise Exception(f"Aggregate function with {len(arg_names)} args")
                expr_str = f"SELECT {mapping.local_name}({arg_names[0]}) FROM my_table;"
            else:
                expr_str = (
                    f"SELECT {mapping.local_name}({joined_arg_names}) FROM my_table;"
                )

            result = self.ctx.sql(expr_str).collect()[0].columns[0].to_pylist()

            if len(result) != 1:
                raise Exception("Scalar function with one row output more than one row")
            result = result[0]

            if case.result == "undefined":
                return SqlCaseResult.success()
            elif case.result == "error":
                return SqlCaseResult.unexpected_pass(str(result))
            elif case.result == "nan":
                if math.isnan(result):
                    return SqlCaseResult.success()
            # Issues with python float comparison:
            # https://tutorpython.com/python-mathisclose/#The_problem_with_using_for_float_comparison
            # https://stackoverflow.com/questions/5595425/what-is-the-best-way-to-compare-floats-for-almost-equality-in-python
            # Datafusion bug with float when converting from a dataframe to a pylist:
            # https://github.com/apache/arrow-datafusion/issues/9950
            elif case.result.type.startswith('fp') and case.result.value:
                if math.isclose(result, case.result.value, rel_tol=1e-6):
                    return SqlCaseResult.success()
            else:
                if result == case.result.value:
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
        except Exception as err:
            return SqlCaseResult.error(str(err))
        finally:
            self.ctx.deregister_table("my_table")
