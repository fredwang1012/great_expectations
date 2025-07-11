from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, List, Literal, Type, Union, overload
from urllib import parse

from great_expectations._docs_decorators import public_api
from great_expectations.compatibility import pydantic
from great_expectations.compatibility.pydantic import AnyUrl
from great_expectations.compatibility.sqlalchemy import (
    sqlalchemy as sa,
)
from great_expectations.compatibility.typing_extensions import override
from great_expectations.datasource.fluent.config_str import ConfigStr
from great_expectations.datasource.fluent.interfaces import (
    DataAsset,
    TestConnectionError,
)
from great_expectations.datasource.fluent.sql_datasource import (
    QueryAsset as SqlQueryAsset,
)
from great_expectations.datasource.fluent.sql_datasource import (
    SQLDatasource,
)
from great_expectations.datasource.fluent.sql_datasource import (
    TableAsset as SqlTableAsset,
)
from great_expectations.execution_engine import SqlAlchemyExecutionEngine
from great_expectations.core.batch_spec import BatchSpec

if TYPE_CHECKING:
    from sqlalchemy.sql import quoted_name  # noqa: TID251 # type-checking only

    from great_expectations.compatibility import sqlalchemy
    from great_expectations.compatibility.pydantic.networks import Parts
    from great_expectations.core.config_provider import _ConfigurationProvider


def _parse_param_from_query_string(param: str, query: str) -> str | None:
    url_components = parse.urlparse(query)
    path = str(url_components.path)
    parse_results: dict[str, list[str]] = parse.parse_qs(path)
    path_results = parse_results.get(param, [])

    if not path_results:
        return None
    if len(path_results) > 1:
        raise ValueError(f"Only one `{param}` query entry is allowed")  # noqa: TRY003 # FIXME CoP
    return path_results[0]


class _UrlQueryError(pydantic.UrlError):
    """
    Custom Pydantic error for missing query in DatabricksDsn.
    """

    code = "url.query"
    msg_template = "URL query is invalid or missing"


class _UrlHttpPathError(pydantic.UrlError):
    """
    Custom Pydantic error for missing http_path in DatabricksDsn query.
    """

    code = "url.query.http_path"
    msg_template = "'http_path' query param is invalid or missing"


class _UrlCatalogError(pydantic.UrlError):
    """
    Custom Pydantic error for missing catalog in DatabricksDsn query.
    """

    code = "url.query.catalog"
    msg_template = "'catalog' query param is invalid or missing"


class _UrlSchemaError(pydantic.UrlError):
    """
    Custom Pydantic error for missing schema in DatabricksDsn query.
    """

    code = "url.query.schema"
    msg_template = "'schema' query param is invalid or missing"


class DatabricksDsn(AnyUrl):
    allowed_schemes = {
        "databricks",
    }
    query: str  # if query is not provided, validate_parts() will raise an error

    @classmethod
    @override
    def validate_parts(cls, parts: Parts, validate_port: bool = True) -> Parts:
        """
        Overridden to validate additional fields outside of scheme (which is performed by AnyUrl).
        """
        query = parts["query"]
        if query is None:
            raise _UrlQueryError()

        http_path = _parse_param_from_query_string(param="http_path", query=query)
        if http_path is None:
            raise _UrlHttpPathError()

        catalog = _parse_param_from_query_string(param="catalog", query=query)
        if catalog is None:
            raise _UrlCatalogError()

        schema = _parse_param_from_query_string(param="schema", query=query)
        if schema is None:
            raise _UrlSchemaError()

        return AnyUrl.validate_parts(parts=parts, validate_port=validate_port)

    @overload
    @classmethod
    def parse_url(
        cls, url: ConfigStr, config_provider: _ConfigurationProvider = ...
    ) -> DatabricksDsn: ...

    @overload
    @classmethod
    def parse_url(
        cls, url: str, config_provider: _ConfigurationProvider | None = ...
    ) -> DatabricksDsn: ...

    @classmethod
    def parse_url(
        cls, url: ConfigStr | str, config_provider: _ConfigurationProvider | None = None
    ) -> DatabricksDsn:
        if isinstance(url, ConfigStr):
            assert config_provider, "`config_provider` must be provided"
            url = url.get_config_value(config_provider=config_provider)
        parsed_url = pydantic.parse_obj_as(DatabricksDsn, url)
        return parsed_url


class DatabricksTableAsset(SqlTableAsset):
    @pydantic.validator("table_name")
    @override
    def _resolve_quoted_name(cls, table_name: str) -> str | quoted_name:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"VALIDATOR DEBUG: _resolve_quoted_name called with table_name={table_name!r}")
        
        from great_expectations.compatibility import sqlalchemy

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function] # FIXME CoP
            logger.warning(f"VALIDATOR DEBUG: sqlalchemy.quoted_name is available")
            
            # Always check the actual string value, regardless of whether it's already quoted_name
            table_name_str = str(table_name)
            table_name_is_quoted: bool = cls._is_bracketed_by_quotes(table_name_str)
            logger.warning(f"VALIDATOR DEBUG: table_name_is_quoted={table_name_is_quoted}")

            # Check if the table name needs special escaping for Databricks
            # This includes names that start with digits or contain special characters
            starts_with_digit = bool(re.match(r"^\d", table_name_str))
            has_special_chars = bool(re.search(r"[.\s\-#@]", table_name_str))
            needs_quoting = (
                table_name_is_quoted or
                starts_with_digit or
                has_special_chars
            )
            logger.warning(f"VALIDATOR DEBUG: needs_quoting={needs_quoting} (starts_with_digit={starts_with_digit}, has_special_chars={has_special_chars})")

            if needs_quoting:
                # For Databricks, include backticks in the value so SQLAlchemy uses them
                clean_table_name = table_name_str.strip('"').strip("'").strip("`")
                result = sqlalchemy.quoted_name(
                    value=f"`{clean_table_name}`",
                    quote=False,  # Don't let SQLAlchemy add more quotes
                )
                logger.warning(f"VALIDATOR DEBUG: Created quoted_name with backticks: {result!r}")
                return result
            else:
                # Standard table that doesn't need special escaping
                if isinstance(table_name, sqlalchemy.quoted_name):
                    logger.warning(f"VALIDATOR DEBUG: table_name is already standard quoted_name, returning as-is")
                    return table_name
                else:
                    result = sqlalchemy.quoted_name(
                        value=table_name,
                        quote=False,
                    )
                    logger.warning(f"VALIDATOR DEBUG: Created standard quoted_name: {result!r}")
                    return result
        else:
            logger.warning(f"VALIDATOR DEBUG: sqlalchemy.quoted_name not available, returning plain string")
        
        return table_name

    @pydantic.validator("schema_name")
    def _resolve_quoted_schema_name(cls, schema_name: str | None) -> str | quoted_name | None:
        if schema_name is None:
            return schema_name

        schema_name_is_quoted: bool = cls._is_bracketed_by_quotes(schema_name)

        from great_expectations.compatibility import sqlalchemy

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function] # FIXME CoP
            if isinstance(schema_name, sqlalchemy.quoted_name):
                return schema_name

            # Check if the schema name needs special escaping for Databricks
            # This includes names that start with digits or contain special characters
            schema_name_str = str(schema_name)
            needs_quoting = (
                schema_name_is_quoted or
                re.match(r"^\d", schema_name_str) or
                re.search(r"[.\s\-#@]", schema_name_str)
            )

            if needs_quoting:
                # For Databricks, include backticks in the value so SQLAlchemy uses them
                clean_schema_name = schema_name_str.strip('"').strip("'").strip("`")
                return sqlalchemy.quoted_name(
                    value=f"`{clean_schema_name}`",
                    quote=False,  # Don't let SQLAlchemy add more quotes
                )
            else:
                # Standard schema that doesn't need special escaping
                return sqlalchemy.quoted_name(
                    value=schema_name,
                    quote=False,
                )
        return schema_name

    @override 
    def _create_batch_spec_kwargs(self) -> dict[str, Any]:
        """Override to handle quoted_name objects properly for Databricks."""
        from typing import Any
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Use fallback logic like qualified_name and as_selectable
        schema_name = self.schema_name
        if schema_name is None and hasattr(self.datasource, 'schema_') and self.datasource.schema_:
            schema_name = self.datasource.schema_
        
        table_name_str = str(self.table_name)
        logger.warning(f"DATABRICKS DEBUG: table_name={self.table_name!r}, str(table_name)='{table_name_str}'")
        
        return {
            "type": "table",
            "data_asset_name": self.name,
            "table_name": table_name_str,
            "schema_name": str(schema_name) if schema_name else None,
            "batch_identifiers": {},
        }

    @staticmethod
    @override
    def _is_bracketed_by_quotes(target: str) -> bool:
        """Returns True if the target string is bracketed by quotes.

        Arguments:
            target: A string to check if it is bracketed by quotes.

        Returns:
            True if the target string is bracketed by quotes.
        """
        # TODO: what todo with regular quotes? Error? Warn? "Fix"?
        return target.startswith("`") and target.endswith("`")


class DatabricksExecutionEngine(SqlAlchemyExecutionEngine):
    """Custom execution engine for Databricks that handles special table name quoting."""
    
    def _subselectable(self, batch_spec: BatchSpec) -> sqlalchemy.Selectable:
        """Override to handle Databricks-specific table name quoting."""
        from great_expectations.compatibility.sqlalchemy import sqlalchemy as sa
        
        table_name = batch_spec.get("table_name")
        query = batch_spec.get("query")
        selectable: sqlalchemy.Selectable
        
        if table_name:
            schema_name = batch_spec.get("schema_name", None)
            
            # Handle Databricks table name quoting
            if self.dialect_name == "databricks":
                import re
                
                # Convert table_name to string and check if it needs Databricks backticks
                table_name_str = str(table_name)
                clean_table_name = table_name_str.strip('"').strip("'").strip("`")
                
                # Check if needs special quoting
                needs_quoting = (
                    re.match(r"^\d", clean_table_name) or 
                    re.search(r"[.\s\-#@]", clean_table_name)
                )
                
                if needs_quoting:
                    # For Databricks, create table with properly quoted name
                    from great_expectations.compatibility import sqlalchemy
                    if sqlalchemy.quoted_name:
                        table_name = sqlalchemy.quoted_name(f"`{clean_table_name}`", quote=False)
                
                # Handle schema similarly
                if schema_name:
                    schema_name_str = str(schema_name)
                    clean_schema_name = schema_name_str.strip('"').strip("'").strip("`")
                    
                    needs_schema_quoting = (
                        re.match(r"^\d", clean_schema_name) or 
                        re.search(r"[.\s\-#@]", clean_schema_name)
                    )
                    
                    if needs_schema_quoting:
                        from great_expectations.compatibility import sqlalchemy
                        if sqlalchemy.quoted_name:
                            schema_name = sqlalchemy.quoted_name(f"`{clean_schema_name}`", quote=False)
            
            selectable = sa.table(table_name, schema=schema_name)
        else:
            if not isinstance(query, str):
                raise ValueError(f"SQL query should be a str but got {query}")
            selectable = sa.select(
                sa.text(query.lstrip()[6:].strip().rstrip(";").rstrip())
            ).subquery()

        return selectable


@public_api
class DatabricksSQLDatasource(SQLDatasource):
    """Adds a DatabricksSQLDatasource to the data context.

    Args:
        name: The name of this DatabricksSQL datasource.
        connection_string: The SQLAlchemy connection string used to connect to the Databricks SQL database.
            For example: "databricks://token:<token>@<host>:<port>?http_path=<http_path>&catalog=<catalog>&schema=<schema>""
        assets: An optional dictionary whose keys are TableAsset or QueryAsset names and whose values
            are TableAsset or QueryAsset objects.
    """  # noqa: E501 # FIXME CoP

    # class var definitions
    asset_types: ClassVar[List[Type[DataAsset]]] = [DatabricksTableAsset, SqlQueryAsset]

    type: Literal["databricks_sql"] = "databricks_sql"  # type: ignore[assignment] # FIXME CoP
    connection_string: Union[ConfigStr, DatabricksDsn]

    # These are instance var because ClassVars can't contain Type variables. See
    # https://peps.python.org/pep-0526/#class-and-instance-variable-annotations
    _TableAsset: Type[SqlTableAsset] = pydantic.PrivateAttr(DatabricksTableAsset)

    @property
    @override
    def execution_engine_type(self) -> Type[DatabricksExecutionEngine]:
        """Returns the Databricks-specific execution engine type."""
        return DatabricksExecutionEngine

    @override
    def test_connection(self, test_assets: bool = True) -> None:
        try:
            super().test_connection(test_assets)
        except TestConnectionError as e:
            nested_exception = None
            if e.__cause__ and e.__cause__.__cause__:
                nested_exception = e.__cause__.__cause__

            # Raise specific error informing how to install dependencies only if relevant
            if isinstance(nested_exception, sa.exc.NoSuchModuleError):
                raise TestConnectionError(  # noqa: TRY003 # FIXME CoP
                    "Could not connect to Databricks - please ensure you've installed necessary dependencies with `pip install great_expectations[databricks]`."  # noqa: E501 # FIXME CoP
                ) from e
            raise e  # noqa: TRY201 # FIXME CoP

    @override
    def _create_engine(self) -> sqlalchemy.Engine:
        model_dict = self.dict(
            exclude=self._get_exec_engine_excludes(),
            config_provider=self._config_provider,
        )

        connection_string = model_dict.pop("connection_string")
        # is connection_string was a ConfigStr it's parts will not have been validated yet
        if not isinstance(connection_string, DatabricksDsn):
            connection_string = DatabricksDsn.parse_url(
                url=connection_string, config_provider=self._config_provider
            )

        kwargs = model_dict.pop("kwargs", {})

        http_path = _parse_param_from_query_string(param="http_path", query=connection_string.query)
        assert http_path, "Presence of http_path query string is guaranteed due to prior validation"

        # Databricks connection is a bit finicky - the http_path portion of the connection string needs to be passed in connect_args  # noqa: E501 # FIXME CoP
        connect_args = {"http_path": http_path}
        return sa.create_engine(connection_string, connect_args=connect_args, **kwargs)
