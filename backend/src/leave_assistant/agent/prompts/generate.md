---
id: generate
version: 3
budget: 200
---
Answer the question in <user> using only the numbered documents. Write the citation marker [n] inside the answer text immediately after each statement it supports, for example: "Employees get twenty days per year [1]." Put one number in each bracket: write [1][2], never [1, 2]. Return JSON with: answer (the text including its [n] markers), citations (the list of document numbers you cited) and confidence (high, medium or low). If the documents do not answer the question, return an empty answer, no citations and confidence low.
