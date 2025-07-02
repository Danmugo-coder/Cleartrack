import os
import shutil
import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from django.contrib.auth.models import User
from core.models import SystemBackup

class Command(BaseCommand):
    help = 'Creates a backup of the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            help='Username of the user creating the backup',
        )
        
        parser.add_argument(
            '--notes',
            type=str,
            help='Notes about this backup',
        )

    def handle(self, *args, **options):
        # Create backup directory if it doesn't exist
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            
        # Generate backup filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        db_path = settings.DATABASES['default']['NAME']
        db_filename = os.path.basename(db_path)
        backup_filename = f"{db_filename}_{timestamp}.bak"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        try:
            # Copy the database file
            shutil.copy2(db_path, backup_path)
            
            # Get file size
            size_bytes = os.path.getsize(backup_path)
            
            # Get user if provided
            user = None
            if options['user']:
                try:
                    user = User.objects.get(username=options['user'])
                except User.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f"User {options['user']} not found, backup will be created without user association"))
            
            # Create backup record
            backup = SystemBackup.objects.create(
                backup_file=backup_filename,
                size_bytes=size_bytes,
                created_by=user,
                is_successful=True,
                notes=options.get('notes', '')
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'Successfully created backup: {backup_filename} ({size_bytes / (1024*1024):.2f} MB)')
            )
            
        except Exception as e:
            # Log failed backup attempt
            SystemBackup.objects.create(
                backup_file=backup_filename,
                size_bytes=0,
                is_successful=False,
                notes=f"Error: {str(e)}"
            )
            
            self.stdout.write(
                self.style.ERROR(f'Failed to create backup: {str(e)}')
            ) 
