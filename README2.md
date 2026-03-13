# LaxPredict-RAG: Neural Insights for NCAA Lacrosse
### AI-Powered Analytics & "Talk-to-Your-Data" Interface

## 🎯 Overview
**LaxPredict-RAG** is an end-to-end Machine Learning project that bridges the gap between **predictive statistical modeling** and **Generative AI**. 

By combining the mathematical rigor of `Scikit-learn` with the conversational power of **Retrieval-Augmented Generation (RAG)**, this system doesn't just predict who will win—it explains *why* based on 10+ years of historical NCAA Division I and II data.

## 🧠 The "Why" (Project Mission)
Most sports models are "black boxes"—they give you a number but no context. This project leverages **Explainable AI** to translate complex regression outputs into natural language insights. As an NCAA athlete and Applied Math student, I designed this to solve real-world coaching and scouting questions using modern AI workflows.

---

## 🚀 Technical Stack
| Category | Tools |
| :--- | :--- |
| **Machine Learning** | Python, Scikit-learn, Pandas, NumPy |
| **Generative AI** | LangChain, OpenAI GPT-4o / Llama 3, ChromaDB (Vector Store) |
| **Data Engineering** | BeautifulSoup/Selenium (NCAA.org scraping) |
| **Frontend** | Streamlit |

---

## 🏗️ System Architecture

### 1. Predictive Engine (The Math)
* **Model:** A `RandomForestRegressor` or `XGBoost` model trained on a decade of NCAA box scores.
* **Feature Engineering:** Implements advanced metrics including:
    * **Efficiency Ratings:** Adjusted for pace of play.
    * **Strength of Schedule (SOS):** Mathematically weighted win-loss ratios.
    * **Possession Value:** Calculating the impact of Ground Balls vs. Face-off % on final score margins.

### 2. Knowledge Retrieval (The RAG)
* **Vector Store:** Team performance summaries and model predictions are embedded into **ChromaDB**.
* **Context Injection:** When a user queries the system, the most relevant statistical "chunks" are retrieved to provide ground-truth context to the LLM.

### 3. User Interface (The Chat)
* A **Streamlit** dashboard where users can:
    * Input current "Live" game stats for mid-season predictions.
    * Query the AI: *"Why does the model project a 5-goal margin for the next matchup?"*

---

## 🛠️ Implementation Roadmap

- [ ] **Phase 1: Data Engineering**
    - Scrape and clean 10 years of NCAA D1/D2 Lacrosse data.
    - Perform feature selection to identify key performance indicators (KPIs).
- [ ] **Phase 2: Model Training**
    - Develop the Scikit-learn regression pipeline.
    - Validate using R-Squared and Mean Absolute Error (MAE).
- [ ] **Phase 3: RAG Integration**
    - Build the LangChain pipeline to connect model outputs to an LLM.
    - Implement semantic search via a Vector Database.
- [ ] **Phase 4: Deployment**
    - Build and launch the Streamlit web application.

---

## 📈 Resume Impact (For Recruiters)
* **End-to-End ML Pipeline:** Shows proficiency from data collection to deployment.
* **Explainable AI (XAI):** Demonstrates the ability to make complex math accessible to non-technical users.
* **Domain Specificity:** Highlights the unique intersection of NCAA athletic experience and Applied Mathematics.