# ==========================
# Cell 3 - Sync PostgreSQL Schema
# ==========================

import logging
import os
import re

logging.basicConfig(level=logging.INFO)
TABLE_NAME = os.getenv("POSTGRES_TABLE_NAME", "vblq_metadata")
SCHEMA_NAME = "public"

def sync(conn, document):
    logging.info( ## log sync start
        "Starting PostgreSQL sync: %s",
        document["blob_name"]
    )
    try:

        documents = [document]
        # ==========================
        # Collect metadata fields
        # ==========================

        metadata_fields = set()

        for doc in documents:
            metadata_fields.update(
                doc["metadata"].keys()
            )


        metadata_fields = sorted(metadata_fields)


        # mapping:
        # SharePoint field -> DB column
        column_mapping = {}
        column_types = document.get("metadata_types", {}) if isinstance(document.get("metadata_types", {}), dict) else {}

        for field in metadata_fields:
            column_mapping[field] = normalize_column_name(field)


        print(
            f"Detected metadata fields: {len(metadata_fields)}"
        )


        # ==========================
        # Check table exists
        # ==========================

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = %s
                    AND table_name = %s
                );
                """,
                (
                    SCHEMA_NAME,
                    TABLE_NAME
                )
            )

            table_exists = cur.fetchone()[0]


        # ==========================
        # CREATE TABLE
        # ==========================

        if not table_exists:

            columns = []


            for col, dtype in system_columns.items():
                columns.append(
                    f"{col} {dtype}"
                )


            for field, column_name in column_mapping.items():

                dtype = map_postgres_type(column_types.get(field))

                columns.append(
                    f"{column_name} {dtype}"
                )


            columns.extend(
                [
                    "created_at TIMESTAMPTZ DEFAULT NOW()",
                    "updated_at TIMESTAMPTZ DEFAULT NOW()"
                ]
            )


            create_sql = f"""
            CREATE TABLE {SCHEMA_NAME}.{TABLE_NAME}
            (
                {", ".join(columns)}
            );
            """


            with conn.cursor() as cur:
                cur.execute(create_sql)


            conn.commit()

            print(
                f"✅ Created table {TABLE_NAME}"
            )


        # ==========================
        # ALTER TABLE
        # ==========================

        else:

            print(
                f"ℹ️ Table {TABLE_NAME} already exists"
            )


            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s;
                    """,
                    (
                        SCHEMA_NAME,
                        TABLE_NAME
                    )
                )

                existing_columns = {
                    row[0]
                    for row in cur.fetchall()
                }


            added_columns = 0


            with conn.cursor() as cur:

                for field, column_name in column_mapping.items():

                    dtype = map_postgres_type(column_types.get(field))


                    if column_name not in existing_columns:

                        alter_sql = f"""
                        ALTER TABLE {SCHEMA_NAME}.{TABLE_NAME}
                        ADD COLUMN {column_name} {dtype};
                        """

                        cur.execute(alter_sql)

                        print(
                            f"➕ Added column: {column_name}"
                        )

                        added_columns += 1


            conn.commit()


            if added_columns == 0:

                print(
                    "✅ Schema already up-to-date"
                )

            else:

                print(
                    f"✅ Added {added_columns} columns"
                )


    except Exception as e:

        conn.rollback()

        print(
            "❌ Schema sync failed:"
        )

        print(e)

        raise


    logging.info("PostgreSQL schema sync completed successfully.")


    # ==========================
    # Cell 4 - Sync metadata
    # ==========================

    db_columns = list(system_columns.keys()) + list(column_mapping.values())
    update_columns = [c for c in db_columns if c != "blob_name"]

    insert_sql = f"""
    INSERT INTO {SCHEMA_NAME}.{TABLE_NAME}
    (
        {", ".join(db_columns)}
    )
    VALUES
    (
        {", ".join(["%s"] * len(db_columns))}
    )
    ON CONFLICT (blob_name)
    DO UPDATE
    SET
        {", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])},
        updated_at = NOW()
    WHERE
        {" OR ".join([
            f"{TABLE_NAME}.{c} IS DISTINCT FROM EXCLUDED.{c}"
            for c in update_columns
        ])};
    """

    processed = 0
    applied = 0
    skipped = 0

    with conn.cursor() as cur:

        for doc in documents:

            metadata = doc["metadata"]

            logging.info( ## log metadata
                "metadata=%s",
                metadata
            )

            print(doc["metadata"]) ## log metadata before upsert or skip

            values = [
                doc["blob_name"],
                doc["file_size_bytes"],
                doc["last_modified"],
            ]

            # Thêm toàn bộ metadata theo đúng mapping
            field_types = document.get("metadata_types", {}) or {}

            for source_field in column_mapping:
                value = metadata.get(source_field)
                values.append(normalize_metadata_value(source_field, value, field_types.get(source_field)))

            logging.info("========== PostgreSQL Debug ==========")
            logging.info("Blob: %s", doc["blob_name"])
            logging.info("Metadata: %s", metadata)
            logging.info("DB Columns: %s", db_columns)
            logging.info("Values: %s", values)

            cur.execute(insert_sql, values)

            print(f"UPSERT rowcount = {cur.rowcount}")  ## log rowcount after upsert or skip

            processed += 1

            # rowcount:
            # 1 = INSERT hoặc UPDATE
            # 0 = Conflict nhưng dữ liệu không đổi (SKIP)
            if cur.rowcount == 1:
                applied += 1
            else:
                skipped += 1

    conn.commit()

    logging.info( ## log sync success
    "Finished PostgreSQL sync: %s",
    document["blob_name"]
    )

    print("=" * 60)
    print("Metadata synchronization completed")
    print("=" * 60)
    print(f"Processed : {processed}")
    print(f"Applied   : {applied}")
    print(f"Skipped   : {skipped}")
    print("=" * 60)

# ==========================
# System columns
# ==========================

system_columns = {
    "blob_name": "TEXT PRIMARY KEY",
    "blob_file_size_bytes": "BIGINT",
    "blob_last_modified": "TIMESTAMPTZ"
}


# tránh trùng tên
rename_map = {
    "file_size_bytes": "sharepoint_file_size_bytes",
    "last_modified": "sharepoint_last_modified"
}


def normalize_column_name(name: str) -> str:
    """
    Chuẩn hóa tên column PostgreSQL
    """

    name = rename_map.get(name, name)

    name = name.replace(" ", "_")

    name = re.sub(
        r"[^0-9a-zA-Z_]",
        "_",
        name
    )

    name = re.sub(
        r"_+",
        "_",
        name
    )

    return name.lower()

def delete(conn, blob_name: str):

    with conn.cursor() as cur:

        cur.execute(
            f"""
            DELETE FROM {SCHEMA_NAME}.{TABLE_NAME}
            WHERE blob_name=%s
            """,
            (blob_name,),
        )

    conn.commit()


def map_postgres_type(sharepoint_type: str | None) -> str:
    normalized = (sharepoint_type or "Text").strip().casefold()

    if normalized in {"datetime", "date time", "datetimehidden"}:
        return "TIMESTAMPTZ"

    return "TEXT"


from datetime import datetime

def normalize_metadata_value(field_name: str, value, sharepoint_type: str | None):
    if value is None:
        return None

    normalized_type = (sharepoint_type or "Text").strip().casefold()
    if normalized_type in {"datetime", "date time", "datetimehidden", "date and time"}:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            
            # Danh sách các định dạng có thể nhận từ SharePoint/Blob
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    dt_obj = datetime.strptime(value, fmt)
                    # Chuyển đổi đối tượng datetime thành chuỗi định dạng DD-MM-YYYY
                    return dt_obj.strftime("%d-%m-%Y")
                except ValueError:
                    pass
                    
        # Trường hợp giá trị truyền vào đã là đối tượng datetime sẵn từ thư viện khác
        if isinstance(value, datetime):
            return value.strftime("%d-%m-%Y")
            
        return value

    return value
