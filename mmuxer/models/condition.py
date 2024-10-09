import logging
from abc import abstractmethod
from collections.abc import Sequence
from typing import Any as TypeAny
from typing import ForwardRef, List, Union

from imap_tools import MailMessage
from pydantic import ConfigDict, model_validator

from .common import BaseModel
from .enums import ComparisonOperator


class IBaseCondition(BaseModel):
    model_config = ConfigDict(frozen=True)

    operator: ComparisonOperator = ComparisonOperator.CONTAINS
    headers_only: bool = True

    @abstractmethod
    def get_value(self, message: MailMessage) -> str:
        """Extract value from the message, used as value to which the rule is applied to."""
        pass

    @abstractmethod
    def get_operand(self) -> Union[str, Sequence[str]]:
        """Used to have a common function returning the operand of the rule across subclasses."""
        pass

    def eval(self, message: MailMessage) -> bool:
        """Evaluate the rule"""
        value = self.get_value(message)
        operand = self.get_operand()
        if value:
            logging.debug("Eval %s <%s> %s", operand, self.operator, value)
        if isinstance(operand, str):
            operands = [operand]
        else:
            operands = operand

        return any(self.operator.eval(op, value) for op in operands)

    def to_sieve(self) -> str:
        """Used to render sieve files
        We include here the one used for header conditions
        """
        operand = self.get_operand()
        if isinstance(operand, str):
            operand_str = f'"{operand}"'
        else:
            operand_str = "[" + ", ".join(f'"{op}"' for op in operand) + "]"

        return f'header {self.operator.sieve} "{self.__class__.__name__.lower()}" {operand_str}'

    def __lt__(self, other):
        """Comparison: used for using with boolean.py"""
        return repr(self).__lt__(repr(other))


class From(IBaseCondition):
    FROM: Union[str, frozenset[str]]

    def get_value(self, message: MailMessage):
        return message.from_

    def get_operand(self) -> Union[str, Sequence[str]]:
        return self.FROM

    def __rich_repr__(self):
        yield self.operator.name, self.FROM


class To(IBaseCondition):
    TO: Union[str, frozenset[str]]

    def get_value(self, message: MailMessage):
        return " ".join(message.to)

    def get_operand(self) -> Union[str, Sequence[str]]:
        return self.TO

    def __rich_repr__(self):
        yield self.operator.name, self.TO


class Subject(IBaseCondition):
    SUBJECT: Union[str, frozenset[str]]

    def get_value(self, message: MailMessage):
        return message.subject

    def get_operand(self) -> Union[str, Sequence[str]]:
        return self.SUBJECT

    def __rich_repr__(self):
        yield self.operator.name, self.SUBJECT


class Body(IBaseCondition):
    BODY: str
    headers_only: bool = False

    def get_value(self, message: MailMessage):
        return message.text + message.html

    def get_operand(self) -> str:
        return self.BODY

    def __rich_repr__(self):
        yield self.operator.name, self.BODY

    def to_sieve(self) -> str:
        """Used to render sieve files"""
        return f'body :text {self.operator.sieve} "{self.get_operand()}'


class HeaderField(IBaseCondition):
    model_config = ConfigDict(str_to_lower=True)
    FIELD: str
    VALUE: str

    def get_value(self, message: MailMessage):
        return message.headers.get(self.FIELD, ("",))[0]

    def get_operand(self) -> str:
        return self.VALUE

    def __rich_repr__(self):
        yield self.operator.name, self.VALUE

    @model_validator(mode="after")
    def set_header_class(self):
        class NewClass(self.__class__):
            pass

        NewClass.__name__ = self.FIELD.title()
        NewClass.__qualname__ = f"{__name__}.{NewClass.__name__}"
        self.__class__ = NewClass
        return self


class Header(BaseModel):
    model_config = ConfigDict(frozen=True)
    headers_only: bool = True

    HEADER: HeaderField

    @model_validator(mode="before")
    @classmethod
    def parse_dict(cls, data: TypeAny) -> dict[str, str]:
        if isinstance(data, dict) and isinstance(data.get("HEADER"), dict):
            header = data.get("HEADER")
            operator = data.pop("operator", None)
            if ("FIELD", "VALUE") not in header:
                operator = header.pop("operator", None)
                if len(header) == 1:
                    key, value = header.popitem()
                    header["FIELD"] = key
                    header["VALUE"] = value

                if operator:
                    header["operator"] = operator
        return data

    def get_value(self, message: MailMessage):
        return self.HEADER.get_value(message)

    def get_operand(self) -> str:
        return self.HEADER.get_operand()

    def eval(self, message: MailMessage) -> bool:
        return self.HEADER.eval(message)

    def __rich_repr__(self):
        yield self.HEADER


BaseCondition = Union[From, To, Subject, Body, Header]


def is_base_condition(obj):
    """Useful for isinstance tests with python < 3.10"""
    return (
        isinstance(obj, From)
        or isinstance(obj, To)
        or isinstance(obj, Subject)
        or isinstance(obj, Header)
    )


All = ForwardRef("All")  # type: ignore
Any = ForwardRef("Any")  # type: ignore
Not = ForwardRef("Not")  # type: ignore


class All(BaseModel):
    ALL: List[Union[BaseCondition, All, Any, Not]]

    def eval(self, message: MailMessage):
        return all(operand.eval(message) for operand in self.ALL)

    def __rich_repr__(self):
        for item in self.ALL:
            yield item

    @property
    def headers_only(self) -> bool:
        return all(operand.headers_only for operand in self.ALL)


class Any(BaseModel):
    ANY: List[Union[BaseCondition, All, Any, Not]]

    def eval(self, message: MailMessage):
        return any(operand.eval(message) for operand in self.ANY)

    def __rich_repr__(self):
        for item in self.ANY:
            yield item

    @property
    def headers_only(self) -> bool:
        return all(operand.headers_only for operand in self.ANY)


class Not(BaseModel):
    NOT: Union[BaseCondition, All, Any, Not]

    def eval(self, message: MailMessage):
        return not self.NOT.eval(message)

    def to_sieve(self) -> str:
        """Used to render sieve files"""
        return f"not {self.NOT.to_sieve()}"

    @property
    def headers_only(self) -> bool:
        return self.NOT.headers_only


All.model_rebuild()
Any.model_rebuild()
Not.model_rebuild()

Condition = Union[BaseCondition, All, Any, Not]
