<p align="right">
  <a href="./README.md">English</a> |
  <a href="./README.fa.md">فارسی</a>
</p>

<h1 align="">Karchand</h1>

<p align="">
  <i>Describe your software project. Get an estimated price in Iranian tomans.</i>
</p>

<p align="">
  A freelance software project cost estimation system powered by Machine Learning, semantic search, and feature-based retrieval.
</p>

---

## Overview

Estimating the cost of freelance software projects is challenging. Every project has different requirements, technical complexity, technologies, and pricing expectations—and reliable estimates depend on understanding these differences.

**Karchand** explores three approaches to software project cost estimation:

- **Machine Learning:** Predicts project costs using structured project features.
- **Semantic Similarity-based RAG:** Retrieves projects with similar descriptions using vector search.
- **Feature Similarity-based RAG:** Identifies comparable projects through weighted comparisons of their structured features.

By combining a custom dataset, LLM-powered feature extraction, and multiple estimation strategies, Karchand provides a practical way to explore how AI and data-driven methods can support freelance project pricing.

## About Me

I'm Arsam, a Computer Engineering student and the creator of Karchand. With a background in frontend development, I gradually shifted my focus toward Data Engineering, Machine Learning, and Retrieval-Augmented Generation (RAG).

Karchand brings these interests together in a project involving data collection, dataset preparation, feature engineering, predictive modeling, vector search, and LLM integration.

Explore more of my work on my [GitHub profile](https://github.com/the-mr-poarbear).

## System Workflow

Users describe their software project and choose one of the three estimation methods. The backend processes the input using the selected approach and returns an estimated price along with an explanation.

Each method uses the same project description but follows a different estimation pipeline.

<img width="464" alt="Karchand system architecture" src="https://github.com/user-attachments/assets/2b7454b7-9216-4b82-ba1d-cf15eccf14f9" />

## Dataset Engineering

### Data Collection

Since no ready-made dataset was available for freelance software project cost estimation, I built a custom dataset by collecting, cleaning, and structuring project data from two Iranian freelance platforms: **Ponisha** and **Karlancer**.

Data was collected by analyzing the APIs used by both platforms and extracting the required information from their responses. The collected data was stored in a relational database designed to support future expansion to additional platforms and project categories.

### Database Design

A relational database was designed to organize project information and model the relationships between projects, categories, skills, freelancers, and organizations.

The database consists of seven main tables, providing a structured foundation for dataset preparation and analysis.

<img width="670" alt="Karchand database schema" src="https://github.com/user-attachments/assets/76ea46b8-02e1-4668-8f5d-d3a135bfa571" />

### Taxonomy and Feature Extraction

To transform unstructured project descriptions into structured data, I developed a custom taxonomy for extracting project characteristics relevant to cost estimation.

The taxonomy includes:

- **85 numerical features**, scored from 0 to 5
- **22 system types**
- **4 technology categories**
- **9 boolean variables**
- **6 categorical variables** describing project type and complexity

An LLM processes each project description and extracts relevant features according to the predefined taxonomy and a set of rules designed to improve consistency and reduce unsupported assumptions.

To accelerate processing, projects were analyzed in batches of five using parallel workers, with concurrency adjusted between 1 and 16 based on API rate limits and response speed.

Extracting features for approximately **20,000 projects took around 14 days**. The resulting features were then validated and standardized to support machine learning, structured comparisons, and feature-based retrieval.

## Cost Estimation Methods

### 1. Machine Learning

Structured features extracted using the custom taxonomy serve as inputs for training and evaluating three cost prediction models:

- Linear Regression
- CatBoost
- Multilayer Neural Network

This approach predicts project costs directly from their extracted characteristics.

### 2. Semantic Similarity-based RAG

Project descriptions are converted into embeddings using `intfloat/multilingual-e5-small` and stored in **Qdrant**.

When a user submits a project description, the system retrieves the **10 most semantically similar projects** using cosine similarity. Their descriptions and pricing information are passed to an LLM, which estimates the project's cost in Iranian tomans.

The response is returned as JSON containing `estimated_price` and `reasoning`.

### 3. Feature Similarity-based RAG

An LLM extracts structured features from the submitted project using the predefined taxonomy.

The system then ranks existing projects using weighted similarity scores across functionality, application type, technologies, and complexity. The **top 10 matching projects** are retrieved, and their details and prices are provided to an LLM to generate the final estimate.

The output includes `estimated_price` and `reasoning` in JSON format.

## User Interface

### Project Input

Users enter a project description and select their preferred estimation method. The same input form supports all three approaches, so switching methods requires no additional information.

The interface includes three main components:

1. Project description input
2. Estimation method selection
3. Submit button

<img width="476" alt="Karchand project input interface" src="https://github.com/user-attachments/assets/05c7fbf2-6a67-479c-85ec-9128bb395d0a" />

### Estimation Results

After processing, the system displays the estimation results through three main modules:

1. **Estimated Price and Reasoning** — The predicted cost and the LLM-generated explanation.
2. **Project Features** — The extracted characteristics and project settings used in the analysis.
3. **Similar Projects** — Comparable projects retrieved by the selected similarity-based method.

<img width="667" alt="Karchand estimation results interface" src="https://github.com/user-attachments/assets/49c1e31a-69f6-416a-a2b5-e1f8678395af" />

---

## Key Technical Highlights

- Collected and structured data from two freelance platforms.
- Built a relational dataset containing approximately 20,000 projects.
- Designed a custom taxonomy for LLM-powered feature extraction.
- Implemented and evaluated three machine learning models.
- Integrated multilingual embeddings and Qdrant vector search.
- Developed two retrieval-based estimation strategies using semantic and structured feature similarity.
- Integrated LLMs to generate price estimates and supporting reasoning.

## Technologies

**Python · SQL · Machine Learning · CatBoost · LLMs · RAG · Qdrant · Vector Embeddings · Relational Databases**

## Author

**Arsam Amirsoleimani**

Computer Engineering student focused on Data Engineering, Machine Learning, and AI-powered applications.

- [GitHub Profile](https://github.com/the-mr-poarbear)
