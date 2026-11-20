-- A face a user untagged after recognition.py auto-matched it to
-- `person_id` ("this is NOT that person"). Unlike a faces row it survives
-- re-detection (faces get fresh ids), because it carries the crop itself:
-- recognition.py embeds `thumbnail` and uses it as a negative example.
-- Only auto tags are recorded (people.untag_face). match_confidence /
-- rejected_at_threshold: the wrong match's score and the match threshold
-- then in force. last_vetoed_*: the most recent time this row blocked a
-- candidate (NULL = never has). No FK; deleted explicitly.
CREATE TABLE IF NOT EXISTS face_rejections (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    photo_id TEXT NOT NULL,
    x1 DOUBLE PRECISION NOT NULL,
    y1 DOUBLE PRECISION NOT NULL,
    x2 DOUBLE PRECISION NOT NULL,
    y2 DOUBLE PRECISION NOT NULL,
    thumbnail BYTEA NOT NULL,
    match_confidence DOUBLE PRECISION,
    rejected_at_threshold DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_vetoed_score DOUBLE PRECISION,
    last_vetoed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_face_rejections_person_id ON face_rejections (person_id);
CREATE INDEX IF NOT EXISTS idx_face_rejections_photo_id ON face_rejections (photo_id);
