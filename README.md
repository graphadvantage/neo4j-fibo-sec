# neo4j-fibo-sec

Neo4j knowledge graph that links the FIBO ontology to SEC CFR Title 17 and a Streamlit app to explore the resulting governance links.

This demonstrates the use of Neo4j for determining compliance risk by comparing dense regulatory frameworks (here SEC Title 17 CFR) to financial institition business processes (here using the FIBO ontology as a proxy for named processes/entities).  

SEC Title 17 CFR consists of 9704 paragraphs.

FIBO ontology consistes of 3109 classes.

The governance coverage is determined from matching vector embeddings of Paragraph to Class to build a `[:GOVERNS]` relationship.  This could be extended further using a combination of entity resolution methods, including NER and fulltext indexes.  Here native Neo4j vector indexing is used for simplicity.

```
(sec:Paragraph)-[GOVERNS]->(fibo:Class)
```

**Live Demo**

https://graphadvantage-neo4j-fibo-sec-streamlit-app-o8qli5.streamlit.app/

**Neo4j Database**

https://drive.google.com/file/d/13YUGAb8wt3PQoxHAAcf-bocHRXNegw4B/view?usp=drive_link


**Installation**
1. Create a virtualenv and install deps: `python -m venv .venv`, `source .venv/bin/activate`, `pip install -r requirements.txt`.
2. Start Neo4j 5.x and load the graph by restoring `neo4j-2026-03-02T02-25-42.dump` or by running `sec_fibo_loader.ipynb` and applying `indexes.cyp`.
If you want to run the complete build pipeline, you'll need to pdate the `.env` file with your keys. 
3. Add Streamlit secrets in `.streamlit/secrets.toml`: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
4. Run the app: `streamlit run streamlit_app.py`.

**Governance Logic**
- Each `FIBO:Class` has a label embedding (`labelVector`). SEC text/paragraph nodes have text embeddings and are indexed for ANN search.
- For each FIBO class, the top-K SEC nodes are retrieved from the vector index; any with cosine similarity above `MIN_SCORE` get a `[:GOVERNS {similarity_score, method:'vector'}]` edge to the class.
- Optional rollup: for each `Section`, take the max similarity from its `Text` or `Paragraph` GOVERNS links and write `[:GOVERNS {similarity_score, method:'vector_rollup'}]` from the `Section` to the FIBO class.

**Resources**

https://spec.edmcouncil.org/fibo/ontology

https://www.ecfr.gov/current/title-17


