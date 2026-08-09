-- 0003_labels_is_genai_ml_relevant.sql — persist the relevance flag from HeadlineLabel.
ALTER TABLE labels ADD COLUMN is_genai_ml_relevant BOOLEAN;
