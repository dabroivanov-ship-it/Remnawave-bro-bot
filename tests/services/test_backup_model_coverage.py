"""Guard: every mapped ORM table (except optional logs) is listed for in-app backup."""

from app.database.models import Base, MonitoringLog
from app.services.backup_service import BackupService


OPTIONAL_BACKUP_TABLES = {
    MonitoringLog.__tablename__,
}


def test_all_orm_tables_are_in_backup_models_or_associations():
    service = BackupService(bot=None)

    covered = {model.__tablename__ for model in service._base_backup_models}
    covered.update(service.association_tables.keys())
    covered.update(OPTIONAL_BACKUP_TABLES)

    mapped = set(Base.metadata.tables.keys())
    missing = sorted(mapped - covered)

    assert not missing, f'Tables missing from backup coverage: {missing}'


def test_guide_tables_seeded_on_install_are_in_backup():
    """Hub/categories/pages are created by migration 0075 and must round-trip in backups."""
    service = BackupService(bot=None)
    covered = {model.__tablename__ for model in service._base_backup_models}
    assert {'guide_hub_settings', 'guide_categories', 'guide_pages'} <= covered
