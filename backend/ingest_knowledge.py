import argparse
import json

from rag import ingest_directory, knowledge_base_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest manifest-listed source documents into local Chroma."
    )
    parser.add_argument("--documents", required=True, help="Knowledge-base root containing the manifest-listed PDF/TXT files")
    parser.add_argument("--manifest", required=True, help="JSON source manifest with document metadata and optional manual sources")
    args = parser.parse_args()
    result = ingest_directory(args.documents, args.manifest)
    print(json.dumps({"ingested": result, "status": knowledge_base_status()}, indent=2))


if __name__ == "__main__":
    main()
