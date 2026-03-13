# Lacrosse Analytics: Predictive Modeling + RAG Chatbot
### Using Scikit-learn and RAG to analyze NCAA D1/D2 performance

## 🏒 Project Motivation
As an NCAA lacrosse athlete and an Applied Math student, I’ve noticed that most sports analytics are either too simple or too "black box." You get a prediction, but no explanation of which stats actually drove that outcome. 

I built this project to bridge that gap. It uses a historical regression model to predict game outcomes and a RAG (Retrieval-Augmented Generation) system to explain those predictions in plain English.

---

## 🛠 My Tech Stack
* **Data/Math:** Python, Scikit-learn (Random Forest), Pandas, NumPy
* **AI/LLM:** LangChain, ChromaDB (Vector Store), OpenAI API
* **Web/UI:** Streamlit
* **Data Source:** Custom scraper for NCAA.org historical stats

---

## 🧠 How It Works
The project is split into two main parts: the "Predictor" and the "Explainer."

### Part 1: The Statistical Model (The Math)
Using 10+ years of NCAA box scores, I built a `Scikit-learn` model to predict win probabilities. 
* **Feature Engineering:** I didn't just use goals and assists. I focused on metrics that actually win games—like possession value (Face-off % vs. Turnovers) and clearing efficiency.
* **The Goal:** To identify which "hidden stats" correlate most strongly with a win in D1/D2 play.

### Part 2: The RAG Interface (The "Talk to Your Data")
Instead of just looking at a spreadsheet of predictions, you can chat with the data.
* I stored team stats and model outputs in a **ChromaDB** vector database.
* The system uses **LangChain** to pull relevant team context and feed it to an LLM.
* **Example:** You can ask, "Why does the model think [Team X] is an underdog this weekend?" and the bot will point to specific stats like their man-down defense or recent shooting percentage.

---

## 📋 My To-Do List (Project Roadmap)

- [ ] **Data Collection:** Scrape 10 years of D1/D2 stats from the NCAA site. (In Progress)
- [ ] **Feature Engineering:** Calculate advanced metrics (Efficiency, SOS weighting).
- [ ] **Modeling:** Train and tune the Scikit-learn regressor.
- [ ] **RAG Setup:** Build the vector database and connection to the LLM.
- [ ] **Deployment:** Put it all together in a Streamlit dashboard.

---

## 💡 Why This Matters
This project isn't just about coding; it's about **Explainable AI**. It shows that I can take raw numbers, find the mathematical signal in the noise, and then use Generative AI to make those insights useful for a coach or a scout.