"""Quick CLI demo: Akola Marathi walkthrough + SQLite restart resume + approval gate."""

from __future__ import annotations

import tempfile
from pathlib import Path

from langgraph.types import Command

from msme_saathi import EnterpriseProfile, build_graph, export_mermaid, sqlite_checkpointer


def main() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="msme_saathi_")) / "checkpoints.sqlite"
    cfg = {"configurable": {"thread_id": "demo-akola-01"}}

    print("=== Graph Mermaid ===\n" + export_mermaid())

    initial_state = {
        "language": "mr",
        "user_query": "अकोला मध्ये प्रिसिजन इंजिनिअरिंग युनिट आहे, ₹35 लाख गुंतवणूक आणि सोलर पॅनेल्स बसवायचे आहेत.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-AKOLA-01",
            enterprise_name="Sahyadri Precision Tools Pvt Ltd",
            uploaded_documents=[
                {"document_type": "udyam_certificate", "enterprise_name": "Sahyadri Precision Tools Pvt Ltd"},
                {"document_type": "gstin_certificate", "enterprise_name": "Sahyadri Precision Works"},
            ],
        ),
    }

    # Session 1: runs until intake follow-up interrupt()
    with sqlite_checkpointer(db_path) as saver:
        g = build_graph(checkpointer=saver)
        r1 = g.invoke(initial_state, config=cfg)
        print("\n=== Step 1: Intake Replan Interrupt ===")
        print(r1["__interrupt__"][0].value)

    # Session 2 (fresh SQLite connection): resume intake -> hits approval_gate interrupt()
    with sqlite_checkpointer(db_path) as saver:
        g = build_graph(checkpointer=saver)
        r2 = g.invoke(
            Command(resume={"udyam_registered": True, "enterprise_scale": "micro", "commercial_production_date": "2024-06-01"}),
            config=cfg,
        )
        print("\n=== Step 2: Resumed from SQLite -> Paused at Approval Gate ===")
        print(r2["__interrupt__"][0].value["draft"])

    # Session 3 (fresh SQLite connection): approve draft -> mock-portal submit
    with sqlite_checkpointer(db_path) as saver:
        g = build_graph(checkpointer=saver)
        r3 = g.invoke(Command(resume={"decision": "approve"}), config=cfg)
        print("\n=== Step 3: Approved & Submitted ===")
        print(r3["final_response"])


if __name__ == "__main__":
    main()
