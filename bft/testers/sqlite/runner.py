import math
import sqlite3
from typing import Dict, NamedTuple

from bft.cases.runner import SqlCaseResult, SqlCaseRunner
from bft.cases.types import Case, CaseLiteral
from bft.dialects.types import SqlMapping
from bft.utils.utils import type_to_dialect_type

type_map = {
    "i8": "TINYINT",
    "i16": "SMALLINT",
    "i32": "INT",
    "i64": "HUGEINT",
    "fp32": "REAL",
    "fp64": "REAL",
    "boolean": "BOOLEAN",
    "string": "TEXT",
}


def type_to_sqlite_type(type: str):
    return type_to_dialect_type(type, type_map)


def literal_to_str(lit: str | int | float):
    if lit is None:
        return "null"
    elif lit in [float("inf"), "inf"]:
        return "9e999"
    elif lit in [float("-inf"), "-inf"]:
        return "-9e999"
    return str(lit)


def flatten(l: list):
    return [item for sublist in l for item in sublist]


def extract_argument_values(case: Case, mapping: SqlMapping):
    arg_vals_list = []
    for arg in case.args:
        arg_vals = []
        if arg.type == "string" and arg.value is not None:
            arg_vals.append("'" + literal_to_str(arg.value) + "'")
        elif mapping.aggregate:
            for value in arg.value:
                arg_vals.append(literal_to_str(value))
        else:
            arg_vals.append(literal_to_str(arg.value))
        arg_vals_list.append(arg_vals)
    return arg_vals_list


class SqliteRunner(SqlCaseRunner):
    def __init__(self, dialect):
        super().__init__(dialect)
        self.conn = sqlite3.connect(":memory:")

    def run_sql_case(self, case: Case, mapping: SqlMapping) -> SqlCaseResult:
        self.conn.execute("BEGIN;")

        try:
            arg_defs = []
            for idx, arg in enumerate(case.args):
                arg_type = type_to_sqlite_type(arg.type)
                if arg_type is None:
                    return SqlCaseResult.unsupported(f"Unsupported type {arg.type}")
                arg_defs.append(f"arg{idx} {arg_type}")
            schema = ",".join(arg_defs)
            self.conn.execute(f"CREATE TABLE my_table({schema});")

            arg_names = [f"arg{idx}" for idx in range(len(case.args))]

            joined_arg_names = ",".join(arg_names)
            arg_vals_list = extract_argument_values(case, mapping)
            arg_vals = ', '.join(flatten(arg_vals_list))

            if mapping.aggregate:
                for arg_name, arg_vals in zip(arg_names, arg_vals_list):
                    str_arg_vals = ",".join(f"({val})" for val in arg_vals)
                    if arg_vals:
                        self.conn.execute(
                            f"INSERT INTO my_table ({arg_name}) VALUES {str_arg_vals};"
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
            elif mapping.between:
                if len(arg_names) != 3:
                    raise Exception(f"Between function with {len(arg_names)} args")
                expr = f"SELECT {arg_names[0]} BETWEEN {arg_names[1]} AND {arg_names[2]} FROM my_table;"
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
            elif case.result == "nan":
                return SqlCaseResult.error(str(result))
            # Issues with python float comparison:
            # https://tutorpython.com/python-mathisclose/#The_problem_with_using_for_float_comparison
            # https://stackoverflow.com/questions/5595425/what-is-the-best-way-to-compare-floats-for-almost-equality-in-python
            elif case.result.type.startswith("fp") and case.result.value and result:
                if math.isclose(result, case.result.value, rel_tol=1e-7):
                    return SqlCaseResult.success()
            else:
                if result == case.result.value:
                    return SqlCaseResult.success()
                else:
                    return SqlCaseResult.mismatch(str(result))
        except sqlite3.Error as err:
            return SqlCaseResult.error(str(err))
        finally:
            self.conn.rollback()
