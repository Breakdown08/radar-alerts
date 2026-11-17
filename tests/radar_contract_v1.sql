CREATE DATABASE IF NOT EXISTS {contract};

CREATE VIEW IF NOT EXISTS {contract}.steam_players
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT entity_id AS appid, observed_at, toUInt32(value) AS players
FROM {source}.observations FINAL
WHERE source = 'steam' AND entity_type = 'game' AND metric = 'players_current';

CREATE VIEW IF NOT EXISTS {contract}.steam_chart
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT entity_id AS appid, observed_at AS chart_day, toUInt16(value) AS rank
FROM {source}.observations FINAL
WHERE source = 'steam' AND entity_type = 'game' AND metric = 'chart_rank';

CREATE VIEW IF NOT EXISTS {contract}.bluesky_tag_hourly
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT substring(metric, 5) AS tag, toStartOfHour(assumeNotNull(window_start)) AS hour, count() AS posts
FROM {source}.observations FINAL
WHERE source = 'bluesky' AND entity_type = 'post' AND startsWith(metric, 'tag_') AND window_start IS NOT NULL
GROUP BY tag, hour;
