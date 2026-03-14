# Lacrosse Analytics: Predictive Modeling and RAG Interface
### Statistical Analysis of NCAA Division I and II Performance

## Project Overview
This project is a technical exploration into "Explainable AI" within the context of NCAA Lacrosse. While many sports analytics platforms provide raw predictions, they often lack a natural language interface that explains the underlying statistical drivers. 

This system utilizes a historical regression model built with Scikit-learn to predict game outcomes and season success. It then integrates a Retrieval-Augmented Generation (RAG) pipeline to allow users to query the model’s findings using natural language. This provides a bridge between complex mathematical outputs and actionable coaching or scouting insights.

---

## Methodology and Logic

### 1. Data Engineering and Feature Selection
Data is sourced via custom scraping of historical NCAA box scores spanning the last 10 years. The focus is on identifying "high-leverage" features that correlate most strongly with win probability. 

Key metrics analyzed include:
* **Efficiency Ratings:** Normalizing scoring and defensive stats against pace of play (possessions per game).
* **Possession Value Index:** A calculated metric weighing Face-off Win % against Turnover Ratios.
* **Strength of Schedule (SOS) Weighting:** Using a mathematical weight to adjust performance metrics based on opponent difficulty.
* **Clearing and Extra-Man Efficiency:** Analyzing the statistical impact of specialized unit performance on final score margins.

### 2. Predictive Modeling (The Math)
The core of the system is a Scikit-learn pipeline. 
* **Model Selection:** Utilizing Random Forest and Gradient Boosting Regressors to handle the non-linear nature of sports data.
* **Validation:** Models are evaluated using Mean Absolute Error (MAE) and R-Squared values to ensure predictive reliability across different NCAA divisions.
* **Feature Importance:** Using the model to rank which statistical categories are the most significant predictors of success in the modern D1/D2 game.

### 3. Retrieval-Augmented Generation (The Interface)
The RAG component transforms this from a static model into an interactive tool.
* **Vectorization:** Team-specific performance reports and model predictions are embedded into a ChromaDB vector database.
* **Contextual Querying:** When a user asks a question (e.g., "Why is Team X trending upward despite a losing record?"), the system retrieves relevant statistical "chunks" and model weights.
* **Natural Language Output:** An LLM processes this retrieved context to generate a response grounded in the actual data, preventing the "hallucinations" common in standard AI models.

---

## Technical Stack
* **Languages:** Python
* **Machine Learning:** Scikit-learn, Pandas, NumPy
* **Generative AI:** LangChain, ChromaDB, OpenAI API
* **Web Framework:** Streamlit
* **Version Control:** Git

---

## Getting Started

### Phase 1: Data Acquisition (Setup)

1. Install dependencies (includes `curl_cffi`, `cloudscraper`, and `playwright` for 403 bypass):
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Configure scraping in `config/data_config.yaml` (years, divisions, delay, etc.).
   - `archive_progress_every`: how often archive fallback logs team progress
   - `archive_max_runtime_minutes`: watchdog timeout for archive fallback

3. Run the pipeline:
   ```bash
   python run_pipeline.py                    # Full range from config
   python run_pipeline.py --years 2024       # Single year
   python run_pipeline.py --years 2014-2024  # Custom range
   ```

4. Output: `data/raw/` (HTML, gitignored) and `data/processed/` (CSV + Parquet, committed). The repo includes a processed snapshot for 2014–2024; see `data/README.md`.

5. Run a trainability/completeness check on model-ready features:
   ```bash
   python scripts/data_completeness_report.py
   ```

---

## Development Roadmap

### Phase 1: Data Acquisition
* ~~Develop scrapers for stats.ncaa.org to pull 10+ years of Division I and II statistics.~~
* Clean and normalize data to handle seasonal variances and conference changes.

### Phase 2: Statistical Modeling
* Conduct Exploratory Data Analysis (EDA) to find correlations.
* Train, tune, and validate Scikit-learn regression models.
* Export feature importance and prediction results for the RAG pipeline.

### Phase 3: RAG Implementation
* Construct the LangChain pipeline and Vector Store.
* Perform prompt engineering to ensure the AI prioritizes statistical accuracy over general knowledge.

### Phase 4: Deployment
* Launch a Streamlit dashboard that allows for real-time stat input and conversational analysis.