/*
    Additive, idempotent materialization of the approved Tiered50 panel.

    The historical ctl.MddRealtimeSamplingTarget rows 1 through 7 are not
    updated, reseeded, or copied into an active legacy writer.  The 43 new
    rows receive stable identities 8 through 50 in a separate control table.
    Every row is prepared disabled.  A separate, explicitly guarded cutover
    operation must activate them after the offline and live readiness gates
    pass.

    This file is generated from the checked-in TIERED50_PANEL_V1 manifest.
    Its phase, tier, cadence, result-limit, and parent identities are data,
    not values to recalculate at deployment time.
*/

SET NOCOUNT ON;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 51200, 'This materialization must run in CologneTransitIntelligence.', 1;
GO

IF SCHEMA_ID(N'ctl') IS NULL
    THROW 51201, 'The established ctl schema is required.', 1;
GO

SET XACT_ABORT ON;
BEGIN TRANSACTION;
GO

IF EXISTS
(
    SELECT 1
    FROM ctl.MddRealtimeSamplingTarget
    WHERE SamplingTargetId NOT IN (1, 2, 3, 4, 5, 6, 7)
)
    THROW 51204, 'Historical SamplingTargetIds outside 1 through 7 were found; no Tiered50 write is safe.', 1;
GO

IF OBJECT_ID(N'ctl.MddMacTiered50ProductionTarget', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacTiered50ProductionTarget
    (
        SamplingTargetId INT NOT NULL
            CONSTRAINT PK_ctl_MddMacTiered50ProductionTarget PRIMARY KEY CLUSTERED,
        SourceParentStationId NVARCHAR(200) NOT NULL,
        ParentStopPointRef NVARCHAR(200) NOT NULL,
        TargetName NVARCHAR(400) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        SamplingEra VARCHAR(40) NOT NULL,
        Tier VARCHAR(10) NOT NULL,
        PhaseIndex INT NOT NULL,
        PhaseCount INT NOT NULL,
        CadenceMinutes INT NOT NULL,
        NumberOfResults INT NOT NULL,
        ContinuityClassification VARCHAR(80) NOT NULL,
        IsEnabled BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacTiered50ProductionTarget_IsEnabled DEFAULT (0),
        ActivationStatus VARCHAR(20) NOT NULL
            CONSTRAINT DF_ctl_MddMacTiered50ProductionTarget_ActivationStatus DEFAULT ('PREPARED'),
        CreatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacTiered50ProductionTarget_CreatedAtUtc DEFAULT (SYSUTCDATETIME()),
        ActivatedAtUtc DATETIME2(7) NULL,
        CONSTRAINT UQ_ctl_MddMacTiered50ProductionTarget_Source UNIQUE (PanelVersion, SourceParentStationId),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Id CHECK (SamplingTargetId BETWEEN 8 AND 50),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Era CHECK (SamplingEra = 'Tiered50'),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Activation CHECK
            ((IsEnabled = 0 AND ActivationStatus = 'PREPARED') OR
             (IsEnabled = 1 AND ActivationStatus = 'ACTIVE')),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Phase CHECK
            (PhaseIndex >= 0 AND PhaseCount > 0 AND PhaseIndex < PhaseCount),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Cadence CHECK
            (CadenceMinutes IN (5, 15, 30)),
        CONSTRAINT CK_ctl_MddMacTiered50ProductionTarget_Results CHECK
            (NumberOfResults IN (20, 30))
    );
END;
GO

IF COL_LENGTH(N'ctl.MddMacTargetClaim', N'SamplingTargetId') IS NULL
    ALTER TABLE ctl.MddMacTargetClaim ADD SamplingTargetId INT NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetClaimEvent', N'SamplingTargetId') IS NULL
    ALTER TABLE ctl.MddMacTargetClaimEvent ADD SamplingTargetId INT NULL;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacTargetClaim')
      AND name = N'UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket
        ON ctl.MddMacTargetClaim (PanelVersion, SamplingTargetId, LogicalDueBucket)
        WHERE SamplingTargetId IS NOT NULL;
GO

DECLARE @Approved TABLE
(
    SamplingTargetId INT NOT NULL PRIMARY KEY,
    SourceParentStationId NVARCHAR(200) NOT NULL,
    ParentStopPointRef NVARCHAR(200) NOT NULL,
    TargetName NVARCHAR(400) NOT NULL,
    PanelVersion NVARCHAR(100) NOT NULL,
    SamplingEra VARCHAR(40) NOT NULL,
    Tier VARCHAR(10) NOT NULL,
    PhaseIndex INT NOT NULL,
    PhaseCount INT NOT NULL,
    CadenceMinutes INT NOT NULL,
    NumberOfResults INT NOT NULL,
    ContinuityClassification VARCHAR(80) NOT NULL
);

INSERT INTO @Approved
(
    SamplingTargetId, SourceParentStationId, ParentStopPointRef, TargetName,
    PanelVersion, SamplingEra, Tier, PhaseIndex, PhaseCount, CadenceMinutes,
    NumberOfResults, ContinuityClassification
)
VALUES
        (8, N'de:05315:11111', N'de:05315:11111', N'Köln Neumarkt', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (9, N'de:05315:11212', N'de:05315:11212', N'Köln Breslauer Platz/Hbf', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (10, N'de:05315:11610', N'de:05315:11610', N'Köln Rudolfplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (11, N'de:05315:11907', N'de:05315:11907', N'Köln Bf Deutz/Messe LANXESS arena', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (12, N'de:05315:13411', N'de:05315:13411', N'Köln Aachener Str./Gürtel', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (13, N'de:05315:19211', N'de:05315:19211', N'Köln Mülheim Wiener Platz', N'TIERED50_PANEL_V1', 'Tiered50', N'A', 0, 1, 5, 20, N'New Tiered50 member'),
        (14, N'de:05315:11112', N'de:05315:11112', N'Köln Poststr.', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (15, N'de:05315:11209', N'de:05315:11209', N'Köln Appellhofplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (16, N'de:05315:11211', N'de:05315:11211', N'Köln Dom/Hbf', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (17, N'de:05315:11411', N'de:05315:11411', N'Köln Chlodwigplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (18, N'de:05315:11511', N'de:05315:11511', N'Köln Barbarossaplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (19, N'de:05315:11611', N'de:05315:11611', N'Köln Moltkestr.', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (20, N'de:05315:11710', N'de:05315:11710', N'Köln Friesenplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 2, 3, 15, 30, N'New Tiered50 member'),
        (21, N'de:05315:11801', N'de:05315:11801', N'Köln Hansaring', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (22, N'de:05315:11810', N'de:05315:11810', N'Köln Ebertplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (23, N'de:05315:11901', N'de:05315:11901', N'Köln Messe/Deutz Bf', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (24, N'de:05315:14211', N'de:05315:14211', N'Köln Venloer Str./Gürtel', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 1, 3, 15, 30, N'New Tiered50 member'),
        (25, N'de:05315:14611', N'de:05315:14611', N'Köln Bocklemünd', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (26, N'de:05315:15011', N'de:05315:15011', N'Köln Neusser Str./Gürtel', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (27, N'de:05315:16101', N'de:05315:16101', N'Köln Chorweiler', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 2, 3, 15, 30, N'New Tiered50 member'),
        (28, N'de:05315:17701', N'de:05315:17701', N'Köln Wahn S-Bahn', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (29, N'de:05315:18010', N'de:05315:18010', N'Köln Kalk Post', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 0, 3, 15, 30, N'New Tiered50 member'),
        (30, N'de:05315:18011', N'de:05315:18011', N'Köln Kalk Kapelle', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 2, 3, 15, 30, N'New Tiered50 member'),
        (31, N'de:05315:19312', N'de:05315:19312', N'Köln Buchheim Herler Str.', N'TIERED50_PANEL_V1', 'Tiered50', N'B', 2, 3, 15, 30, N'New Tiered50 member'),
        (32, N'de:05315:11410', N'de:05315:11410', N'Köln Ubierring', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 4, 6, 30, 30, N'New Tiered50 member'),
        (33, N'de:05315:11514', N'de:05315:11514', N'Köln Eifelwall/Stadtarchiv', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 0, 6, 30, 30, N'New Tiered50 member'),
        (34, N'de:05315:11809', N'de:05315:11809', N'Köln Reichenspergerplatz', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 1, 6, 30, 30, N'New Tiered50 member'),
        (35, N'de:05315:11909', N'de:05315:11909', N'Köln Deutzer Freiheit', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 3, 6, 30, 30, N'New Tiered50 member'),
        (36, N'de:05315:11914', N'de:05315:11914', N'Köln Deutz Technische Hochschule', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 5, 6, 30, 30, N'New Tiered50 member'),
        (37, N'de:05315:12811', N'de:05315:12811', N'Köln Sürth Bf', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 3, 6, 30, 30, N'New Tiered50 member'),
        (38, N'de:05315:13111', N'de:05315:13111', N'Köln Weißhausstr.', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 5, 6, 30, 30, N'New Tiered50 member'),
        (39, N'de:05315:13113', N'de:05315:13113', N'Köln Sülzburgstr.', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 2, 6, 30, 30, N'New Tiered50 member'),
        (40, N'de:05315:13211', N'de:05315:13211', N'Köln Sülzgürtel', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 3, 6, 30, 30, N'New Tiered50 member'),
        (41, N'de:05315:13221', N'de:05315:13221', N'Köln Klettenbergpark', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 2, 6, 30, 30, N'New Tiered50 member'),
        (42, N'de:05315:13611', N'de:05315:13611', N'Köln Junkersdorf', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 4, 6, 30, 30, N'New Tiered50 member'),
        (43, N'de:05315:13708', N'de:05315:13708', N'Köln Weiden Zentrum', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 0, 6, 30, 30, N'New Tiered50 member'),
        (44, N'de:05315:15113', N'de:05315:15113', N'Köln Slabystr.', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 5, 6, 30, 30, N'New Tiered50 member'),
        (45, N'de:05315:15121', N'de:05315:15121', N'Köln Amsterdamer Str./Gürtel', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 2, 6, 30, 30, N'New Tiered50 member'),
        (46, N'de:05315:18511', N'de:05315:18511', N'Köln Merheim', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 2, 6, 30, 30, N'New Tiered50 member'),
        (47, N'de:05315:19511', N'de:05315:19511', N'Köln Dellbrück Hauptstr.', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 1, 6, 30, 30, N'New Tiered50 member'),
        (48, N'de:05315:19512', N'de:05315:19512', N'Köln Thielenbruch', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 4, 6, 30, 30, N'New Tiered50 member'),
        (49, N'de:05315:19801', N'de:05315:19801', N'Köln Stammheim S-Bahn', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 1, 6, 30, 30, N'New Tiered50 member'),
        (50, N'de:05315:19851', N'de:05315:19851', N'Köln Stammheimer Ring', N'TIERED50_PANEL_V1', 'Tiered50', N'C', 0, 6, 30, 30, N'New Tiered50 member');

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacTiered50ProductionTarget AS existing
    INNER JOIN @Approved AS approved
        ON approved.SamplingTargetId = existing.SamplingTargetId
    WHERE existing.SourceParentStationId <> approved.SourceParentStationId
       OR existing.ParentStopPointRef <> approved.ParentStopPointRef
       OR existing.TargetName <> approved.TargetName
       OR existing.PanelVersion <> approved.PanelVersion
       OR existing.SamplingEra <> approved.SamplingEra
       OR existing.Tier <> approved.Tier
       OR existing.PhaseIndex <> approved.PhaseIndex
       OR existing.PhaseCount <> approved.PhaseCount
       OR existing.CadenceMinutes <> approved.CadenceMinutes
       OR existing.NumberOfResults <> approved.NumberOfResults
       OR existing.ContinuityClassification <> approved.ContinuityClassification
       OR existing.ActivationStatus NOT IN ('PREPARED', 'ACTIVE')
)
    THROW 51202, 'Existing Tiered50 target identity or policy conflicts with the approved manifest.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacTiered50ProductionTarget AS existing
    INNER JOIN @Approved AS approved
        ON approved.SourceParentStationId = existing.SourceParentStationId
    WHERE existing.SamplingTargetId <> approved.SamplingTargetId
)
    THROW 51203, 'Existing Tiered50 parent identity is allocated to a different SQL identity.', 1;

INSERT INTO ctl.MddMacTiered50ProductionTarget
(
    SamplingTargetId, SourceParentStationId, ParentStopPointRef, TargetName,
    PanelVersion, SamplingEra, Tier, PhaseIndex, PhaseCount, CadenceMinutes,
    NumberOfResults, ContinuityClassification, IsEnabled, ActivationStatus
)
SELECT
    approved.SamplingTargetId, approved.SourceParentStationId, approved.ParentStopPointRef,
    approved.TargetName, approved.PanelVersion, approved.SamplingEra, approved.Tier,
    approved.PhaseIndex, approved.PhaseCount, approved.CadenceMinutes,
    approved.NumberOfResults, approved.ContinuityClassification, 0, 'PREPARED'
FROM @Approved AS approved
WHERE NOT EXISTS
(
    SELECT 1
    FROM ctl.MddMacTiered50ProductionTarget AS existing
    WHERE existing.SamplingTargetId = approved.SamplingTargetId
);

IF (SELECT COUNT_BIG(*) FROM ctl.MddMacTiered50ProductionTarget
    WHERE PanelVersion = N'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50') <> 43
    THROW 51205, 'Tiered50 materialization did not reconcile to exactly 43 new rows.', 1;

IF EXISTS
(
    SELECT SamplingTargetId
    FROM ctl.MddMacTiered50ProductionTarget
    WHERE PanelVersion = N'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50'
    GROUP BY SamplingTargetId
    HAVING COUNT_BIG(*) <> 1
)
    THROW 51206, 'Tiered50 production identities are not unique.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacTiered50ProductionTarget
    WHERE PanelVersion = N'TIERED50_PANEL_V1'
      AND SamplingEra = 'Tiered50'
      AND IsEnabled = 1
      AND ActivationStatus <> 'ACTIVE'
)
    THROW 51207, 'Tiered50 target activation status is inconsistent.', 1;
GO

COMMIT TRANSACTION;
GO
