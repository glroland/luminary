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

-- Structured reverse-geocode parts for the sidebar's "By Location" tree.
-- Existing photos stay NULL (shown as "Location Unknown") until "Refresh
-- Locations" is run.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_country TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_state TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_city TEXT;

-- Pets: faces/individuals gain a kind; photos track pet detection.
ALTER TABLE individuals ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person';
ALTER TABLE faces ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person';
ALTER TABLE faces ADD COLUMN IF NOT EXISTS species TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS pet_detection_done BOOLEAN NOT NULL DEFAULT FALSE;

-- Trips (trips.py / trip_detection.py): runs of dated, GPS-tagged photos
-- taken away from home. No FK constraints, same explicit-delete rationale
-- as photo_tags above (storage.delete_photo/purge_all and
-- sync._delete_missing clean up trip_photos/trip_exclusions).
--
-- Where the user lives: auto-detected per calendar year (unconfirmed,
-- replaced on every run) or entered by hand / confirmed (never touched by
-- detection). NULL start/end = unbounded. country/state/city are for
-- display only.
CREATE TABLE IF NOT EXISTS homes (
    id TEXT PRIMARY KEY,
    name TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    radius_km DOUBLE PRECISION,
    start_date TEXT,
    end_date TEXT,
    country TEXT,
    state TEXT,
    city TEXT,
    source TEXT NOT NULL DEFAULT 'auto',
    confirmed BOOLEAN NOT NULL DEFAULT FALSE
);

-- kind: 'trip' (2+ calendar days) | 'outing' (one day). status: 'active' |
-- 'dismissed' (a tombstone: keeps start_at/end_at so detection doesn't
-- recreate it). source/name_source/cover_source: 'auto' | 'manual'.
-- edited: any manual change -- detection never deletes an edited trip.
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_source TEXT NOT NULL DEFAULT 'auto',
    kind TEXT NOT NULL DEFAULT 'trip',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'auto',
    start_at TEXT,
    end_at TEXT,
    cover_photo_id TEXT,
    cover_source TEXT NOT NULL DEFAULT 'auto',
    edited BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- photo_id is the primary key: a photo is in at most one trip.
-- membership: 'auto' (detection may release it) | 'manual' (never).
CREATE TABLE IF NOT EXISTS trip_photos (
    photo_id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL,
    membership TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_trip_photos_trip_id ON trip_photos (trip_id);

-- A photo a user removed from a trip: detection never puts it back.
CREATE TABLE IF NOT EXISTS trip_exclusions (
    trip_id TEXT NOT NULL,
    photo_id TEXT NOT NULL,
    PRIMARY KEY (trip_id, photo_id)
);

-- Movies (docs/video-support.md).
ALTER TABLE photos ADD COLUMN IF NOT EXISTS media_kind TEXT NOT NULL DEFAULT 'image';
ALTER TABLE photos ADD COLUMN IF NOT EXISTS duration_seconds DOUBLE PRECISION;

-- Optional groups for individuals/pets ("By Individual" in the sidebar).
CREATE TABLE IF NOT EXISTS individual_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER
);
ALTER TABLE individuals ADD COLUMN IF NOT EXISTS group_id TEXT;
ALTER TABLE individuals ADD COLUMN IF NOT EXISTS sort_order INTEGER;

-- User-set display rotation (degrees clockwise, 0-359, multiples of 45).
ALTER TABLE photos ADD COLUMN IF NOT EXISTS rotation INTEGER NOT NULL DEFAULT 0;
