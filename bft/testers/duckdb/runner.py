import datetime
import math
from decimal import Decimal, ROUND_DOWN
from typing import Dict, NamedTuple

import duckdb

from bft.cases.runner import SqlCaseResult, SqlCaseRunner
from bft.cases.types import Case
from bft.dialects.types import SqlMapping
from bft.utils.utils import type_to_dialect_type, compareDecimalResult

type_map = {
    "i8": "TINYINT",
    "i16": "SMALLINT",
    "i32": "INTEGER",
    "i64": "BIGINT",
    "fp32": "REAL",
    "fp64": "DOUBLE",
    "boolean": "BOOLEAN",
    "string": "VARCHAR",
    "date": "DATE",
    "time": "TIME",
    "timestamp": "TIMESTAMP",
    "timestamp_tz": "TIMESTAMPTZ",
    "interval": "INTERVAL",
    "decimal": "DECIMAL",
}


def type_to_duckdb_type(type: str):
    return type_to_dialect_type(type, type_map)


def literal_to_str(lit: str | int | float):
    if lit is None:
        return "null"
    elif lit in [math.nan, "nan"]:
        return "'NaN'"
    elif lit in [float("inf"), "inf"]:
        return "'Infinity'"
    elif lit in [float("-inf"), "-inf"]:
        return "'-Infinity'"
    return str(lit)


def is_string_type(arg):
    return (
        arg.type in ["string", "timestamp", "timestamp_tz", "date", "time"]
        and arg.value is not None
    )


def is_datetype(arg):
    return type(arg) in [datetime.datetime, datetime.date, datetime.timedelta]


class DuckDBRunner(SqlCaseRunner):
    def __init__(self, dialect):
        super().__init__(dialect)
        self.conn = duckdb.connect()

    def run_sql_case(self, case: Case, mapping: SqlMapping) -> SqlCaseResult:

        try:
            arg_defs = [
                f"arg{idx} {type_to_duckdb_type(arg.type)}"
                for idx, arg in enumerate(case.args)
            ]
            schema = ",".join(arg_defs)
            self.conn.execute(f"CREATE TABLE my_table({schema});")
            self.conn.execute(f"SET TimeZone='UTC';")

            arg_names = [f"arg{idx}" for idx in range(len(case.args))]
            joined_arg_names = ",".join(arg_names)
            arg_vals_list = list()
            for arg in case.args:
                if is_string_type(arg):
                    arg_vals_list.append("'" + literal_to_str(arg.value) + "'")
                else:
                    arg_vals_list.append(literal_to_str(arg.value))
            arg_vals = ", ".join(arg_vals_list)
            if mapping.aggregate:
                arg_vals_list = list()
                for arg in case.args:
                    arg_vals = ""
                    for value in arg.value:
                        if is_string_type(arg):
                            if value:
                                arg_vals += f"('{literal_to_str(value)}'),"
                            else:
                                arg_vals += f"({literal_to_str(value)}),"
                        else:
                            arg_vals += f"({literal_to_str(value)}),"
                    arg_vals_list.append([arg_vals[:-1]])
                for arg_name, arg_vals in zip(arg_names, arg_vals_list):
                    if len(arg_vals[0]):
                        self.conn.execute(
                            f"INSERT INTO my_table ({arg_name}) VALUES {arg_vals[0]};"
                        )
            else:
                self.conn.execute(
                    f"INSERT INTO my_table ({joined_arg_names}) VALUES ({arg_vals});"
                )

            if mapping.infix:
                if len(arg_names) != 2:
                    raise Exception(f"Infix function with {len(arg_names)} args")
                expr = f"SELECT {arg_names[0]} {mapping.local_name} {arg_names[1]} FROM my_table;"
            elif mapping.postfix:
                if len(arg_names) != 1:
                    raise Exception(f"Postfix function with {len(arg_names)} args")
                expr = f"SELECT {arg_names[0]} {mapping.local_name} FROM my_table;"
            elif mapping.extract:
                if len(arg_names) != 2:
                    raise Exception(f"Extract function with {len(arg_names)} args")
                expr = f"SELECT {mapping.local_name}({arg_vals_list[0]} FROM {arg_names[1]}) FROM my_table;"
            elif mapping.local_name == 'count(*)':
                expr = f"SELECT {mapping.local_name} FROM my_table;"
            elif mapping.aggregate:
                if len(arg_names) < 1:
                    raise Exception(f"Aggregate function with {len(arg_names)} args")
                expr = f"SELECT {mapping.local_name}({arg_names[0]}) FROM my_table;"
            else:
                expr = f"SELECT {mapping.local_name}({joined_arg_names}) FROM my_table;"
            result = self.conn.execute(expr).fetchone()[0]

            if case.result == "undefined":
                return SqlCaseResult.success()
            elif case.result == "error":
                return SqlCaseResult.unexpected_pass(str(result))
            elif str(result) == "nan":
                if case.result == "nan":
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
            # Issues with python float comparison:
            # https://tutorpython.com/python-mathisclose/#The_problem_with_using_for_float_comparison
            # https://stackoverflow.com/questions/5595425/what-is-the-best-way-to-compare-floats-for-almost-equality-in-python
            elif case.result.type.startswith("fp") and case.result.value and result:
                if math.isclose(result, case.result.value, rel_tol=1e-7):
                    return SqlCaseResult.success()
            elif case.result.type.startswith("dec") and case.result.value and result:
                if compareDecimalResult(case.result.value, Decimal(str(result))):
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
            else:
                if result == case.result.value:
                    return SqlCaseResult.success()
                elif is_datetype(result) and str(result) == case.result.value:
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
        except duckdb.Error as err:
            return SqlCaseResult.error(str(err))
        finally:
            self.conn.execute("DROP TABLE my_table")
