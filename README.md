<h1 align="center">Karchand</h1>
<p align="center"><i>Explain your project and see how much it worth in tomans!</i></p>

## Overview
Estimating the cost of freelance software projects is challenging because every project has different requirements, technical complexity, and pricing expectations. 
This project introduces a system that predicts project costs using three approaches: 
- **Machine Learning**
- **Semantic Similarity-based RAG**
-  **Feature Similarity-based RAG**
Each approach uses a different method to analyze project descriptions and estimate their costs.

## Author
Im Arsam and the creator of Karchand platform 
I come from a background of frontend dev but with the rise of llms I slowly shifted twards Data engineering and ML/Rag
You can see my other projects here "my profile"

## How the proccess actually goes
Users enter project details and select one of three cost estimation methods:

- **Machine Learning:** An LLM extracts project features using a predefined taxonomy, and a machine learning model predicts the cost.
- **Semantic Similarity-based RAG:** The system retrieves projects with similar descriptions and uses an LLM to estimate the cost based on their data.
- **Feature Similarity-based RAG:** The system compares structured project features to find similar projects, then uses an LLM to estimate the cost.

Each run uses only the method selected by the user. The diagram below illustrates the system architecture.
<img width="464" height="683" alt="image" src="https://github.com/user-attachments/assets/2b7454b7-9216-4b82-ba1d-cf15eccf14f9" />

### Dataset Creation and Preparation
Since no ready-made dataset was available for freelance software project cost estimation, a custom dataset was built by collecting, cleaning, and structuring project data from two popular Iranian freelance platforms: **Ponisha** and **Karlancer**.

### Data Sources and Collection
Project data was collected automatically by analyzing the APIs used by both platforms and extracting the required information from their responses. This approach enabled structured data collection without relying on webpage content.
The collected data was stored in a relational database designed to support future expansion, including new platforms and project categories.

### Data Collection Implementation
A relational database was implemented to organize project information and manage relationships between projects, categories, skills, freelancers, and organizations. The database consists of seven main tables, with their relationships illustrated in the diagram below.
<img width="670" height="428" alt="image" src="https://github.com/user-attachments/assets/76ea46b8-02e1-4668-8f5d-d3a135bfa571" />
### Taxonomy Extraction
A custom taxonomy was developed to convert unstructured project descriptions into structured features for cost estimation. It includes **85 project features** scored from 0 to 5, **22 system types**, **4 technology categories**, **9 boolean variables**, and **6 categorical variables** describing project type and complexity.
An LLM analyzes each project description and extracts the relevant features according to the predefined taxonomy and a set of rules designed to improve consistency and reduce unsupported assumptions.
To speed up processing, projects were analyzed in batches of five, with parallel workers adjusted between 1 and 16 based on API rate limits and response speed. Extracting taxonomy features for approximately **20,000 projects took around 14 days**.
Finally, the extracted features were validated, standardized, and added to the dataset, enabling structured project comparisons and providing input for both machine learning models and feature-based retrieval methods.
