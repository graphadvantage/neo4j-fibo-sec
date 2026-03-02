//on FIBO prefLabel
CREATE VECTOR INDEX fiboLabelSearch IF NOT EXISTS FOR (n:Class) ON n.labelVector
OPTIONS { indexConfig: {
 `vector.dimensions`: 3072,
 `vector.similarity_function`: 'cosine'
}}

//on SEC Title 17 Paragraphs
CREATE VECTOR INDEX secParagraphSearch IF NOT EXISTS FOR (n:Paragraph) ON n.textVector
OPTIONS { indexConfig: {
 `vector.dimensions`: 3072,
 `vector.similarity_function`: 'cosine'
}}

//on SEC Title 17 Combined Text & Paragraphs
CREATE VECTOR INDEX secTextSearch IF NOT EXISTS FOR (n:SEC) ON n.textVector
OPTIONS { indexConfig: {
 `vector.dimensions`: 3072,
 `vector.similarity_function`: 'cosine'
}}


