import os
from typing import Dict, List, NamedTuple

import pytest

from bft.cases.types import Case, CaseLiteral, Literal, case_to_kernel_str
from bft.core.function import Kernel


type_to_short_type = {
    'required enumeration': 'req',
    'i8': 'i8',
    'i16': 'i16',
    'i32': 'i32',
    'i64': 'i64',
    'fp32': 'fp32',
    'fp64': 'fp64',
    'string': 'str',
    'binary': 'vbin',
    'boolean': 'bool',
    'timestamp': 'ts',
    'timestamp_tz': 'tstz',
    'date': 'date',
    'time': 'time',
    'interval_year': 'iyear',
    'interval_day': 'iday',
    'uuid': 'uuid',
    'fixedchar<N>': 'fchar',
    'varchar<N>': 'vchar',
    'fixedbinary<N>': 'fbin',
    'decimal<P,S>': 'dec',
    'precision_timestamp<P>': 'pts',
    'precision_timestamp_tz<P>': 'ptstz',
    'struct<T1,T2,...,TN>': 'struct',
    'list<T>': 'list',
    'map<K,V>': 'map',
    'map': 'map',
    'any': 'any',
    'any1': 'any1',
    'any2': 'any2',
    'any3': 'any3',
    'user defined type': 'u!name',

    # added to handle parametrized types
    'fixedchar': 'fchar',
    'varchar': 'vchar',
    'fixedbinary': 'fbin',
    'decimal': 'dec',
    'precision_timestamp': 'pts',
    'precision_timestamp_tz': 'ptstz',
    'struct': 'struct',
    'list': 'list',

    # added to handle geometry type
    'geometry': 'geometry',
}

short_type_to_type = {st: lt for lt, st in type_to_short_type.items()}


class DialectKernel(NamedTuple):
    arg_types: List[str]
    result_type: str


class DialectFunction(NamedTuple):
    name: str
    local_name: str
    infix: bool
    postfix: bool
    between: bool
    aggregate: bool
    unsupported: bool
    extract: bool
    required_options: Dict[str, str]
    variadic_min: int
    supported_kernels: List[DialectKernel]


class DialectFile(NamedTuple):
    name: str
    type: str
    scalar_functions: List[DialectFunction]
    aggregate_functions: List[DialectFunction]
    uri_to_func_prefix: Dict[str, str]
    supported_types: Dict[str, str]


class SqlMapping(NamedTuple):
    local_name: str
    infix: bool
    postfix: bool
    between: bool
    aggregate: bool
    unsupported: bool
    extract: bool
    should_pass: bool
    reason: str


class Dialect(object):
    def __init__(self, dialect_file: DialectFile):
        self.name = dialect_file.name
        self.supported_types = dialect_file.supported_types
        self.__scalar_functions_by_name: Dict[str, DialectFunction] = {
            f.name: f for f in dialect_file.scalar_functions
        }
        self.__aggregate_functions_by_name: Dict[str, DialectFunction] = {
            f.name: f for f in dialect_file.aggregate_functions
        }
        self.__func_prefixes: Dict[str, str] = {
            uri: prefix for uri, prefix in dialect_file.uri_to_func_prefix.items()
        }

    def __supports_case_kernel(
            self,
            dfunc: DialectFunction,
            args: List[CaseLiteral],
            result: CaseLiteral | Literal["error", "undefined"],
    ):
        arg_len_matched = False
        for supported_kernel in dfunc.supported_kernels:
            if dfunc.aggregate:
                arg_len = 1
                if len(args) == 0 or args[0].is_not_a_func_arg:
                    arg_len = 0
            else:
                arg_len = len(args)
            if len(supported_kernel.arg_types) != arg_len and dfunc.variadic_min == -1:
                continue
            arg_len_matched = True
            matched = True
            kernel_arg_types = supported_kernel.arg_types
            if dfunc.variadic_min != -1 and len(supported_kernel.arg_types) == 1:
                kernel_arg_types = supported_kernel.arg_types * len(args)

            any_map = {}
            for ktype, arg in zip(kernel_arg_types, args):
                if arg.type != ktype and not ktype.startswith("any"):
                    matched = False
                    break
                if ktype.startswith("any"):
                    if ktype not in any_map:
                        if arg.type not in self.supported_types:
                            matched = False
                            break
                        any_map[ktype] = arg.type
                    elif any_map[ktype] != arg.type:
                        matched = False
                        break
            if matched:
                return None

        if not arg_len_matched:
            raise Exception("Unreachable path.  Supported kernel with different # of types than case")
        return f"The dialect {self.name} does not support the kernel {case_to_kernel_str(dfunc.name, args, result)}"

    def __supports_options(self, dfunc: DialectFunction, case: Case):
        for case_opt, case_val in case.options:
            dval = dfunc.required_options.get(case_opt)
            if dval is None:
                # If the dialect does not require an option we assume it supports all values
                continue
            if dval != case_val:
                return f"The dialect {self.name} expects {case_opt}={dval} but {case_opt}={case_val} was requested"
        return None

    def required_options(self, function_name) -> Dict[str, str]:
        dfunc = self.__scalar_functions_by_name.get(function_name, None)
        if(not dfunc):
            dfunc = self.__aggregate_functions_by_name.get(function_name, None)
        return getattr(dfunc, "required_options", None)

    def supports_kernel(self, function_name: str, kernel: Kernel) -> bool:
        dfunc = self.__scalar_functions_by_name.get(function_name, None)
        if dfunc is None:
            return False
        for supported_kernel in dfunc.supported_kernels:
            if len(supported_kernel.arg_types) != len(kernel.arg_types):
                continue
            matched = True
            for ktype, arg_type in zip(supported_kernel.arg_types, kernel.arg_types):
                if arg_type != ktype:
                    matched = False
                    break
            if matched:
                return True
        return False

    def _get_function_name(self, case: Case) -> str:
        prefix = self.__func_prefixes.get(case.base_uri, "")
        if len(prefix) > 0:
            return prefix + "." + case.function
        return case.function

    def mapping_for_case(self, case: Case) -> SqlMapping:
        func_name = self._get_function_name(case)
        dfunc_scalar = self.__scalar_functions_by_name.get(func_name, None)
        dfunc_aggregate = self.__aggregate_functions_by_name.get(func_name, None)
        dfunc = dfunc_scalar or dfunc_aggregate
        if "PYTEST_CURRENT_TEST" not in os.environ:
            if dfunc is None:
                return None
        elif dfunc is None:
            pytest.skip(f"Skipping unsupported function. {case.base_uri}/{case.function}")

        kernel_failure = self.__supports_case_kernel(dfunc, case.args, case.result)
        if kernel_failure is not None:
            return SqlMapping(
                dfunc.local_name,
                dfunc.infix,
                dfunc.postfix,
                dfunc.between,
                dfunc.aggregate,
                True,
                dfunc.extract,
                False,
                kernel_failure,
            )

        option_failure = self.__supports_options(dfunc, case)
        if option_failure is not None:
            return SqlMapping(
                dfunc.local_name,
                dfunc.infix,
                dfunc.postfix,
                dfunc.between,
                dfunc.aggregate,
                dfunc.unsupported,
                dfunc.extract,
                False,
                option_failure,
            )

        return SqlMapping(
            dfunc.local_name,
            dfunc.infix,
            dfunc.postfix,
            dfunc.between,
            dfunc.aggregate,
            dfunc.unsupported,
            dfunc.extract,
            True,
            None,
        )


class DialectsLibrary(object):
    def __init__(self, dialects: List[DialectFile]):
        self.dialects = {dialect.name: Dialect(dialect) for dialect in dialects}

    def get_dialect_by_name(self, name: str) -> Dialect:
        return self.dialects[name]
