# Agents Documentation

This repository follows the agents-only workflow pattern where all changes are managed through agent interactions.

## Repository Structure

### Main Components
- **auction_finder.py**: Logic to crawl auction listings
- **market_crawler.py**: Logic to crawl house market websites  
- **db.py**: Database operations and schema management
- **build_report.py**: Generates HTML visualization from database (house_finder.db)
- **update_all.sh**: Script that runs all scraping processes and builds the report

## Repository Setup

### Virtual Environment
To ensure proper execution, this repository uses a virtual environment:

1. **Create virtual environment** (if not exists):
   ```bash
   python3 -m venv .venv
   ```

2. **Activate virtual environment**:
   ```bash
   source .venv/bin/activate
   ```

3. **Install dependencies** (if not already installed):
   ```bash
   pip install requests beautifulsoup4
   ```

## Repository Maintenance Guidelines

### Updating Documentation
When making changes to the codebase:
1. **Update README.md** to reflect any new features or changes in functionality
2. Ensure all configuration options and usage examples are current
3. Document any new API endpoints or crawler implementations

### Commit Process
1. All changes should be made through agent interactions
2. When committing, ensure the commit message clearly describes:
   - What was changed
   - Why it was necessary
   - How it affects existing functionality

### Testing
Before finalizing changes:
1. Ensure all crawlers still function correctly 
2. Verify database integration works as expected
3. Test that the houses.html generation process is unaffected

## System Integration
All crawlers integrate with the existing database system:
- Listings are stored using the same schema structure
- The houses.html file generation process uses standard filtering parameters 
- Data consistency is maintained across all property sources

## Adding New Crawlers

### Market Home Crawlers (market_crawler.py)
When implementing new property crawlers for market homes:
1. **Focus on District of Lisboa only** (as specified in requirements)
2. **Extract and store the following information** for each listing:
   - freguesia (parish)
   - concelho (municipality) 
   - tipologia (property type)
   - price (published_price_eur)
   - date published (published_at)
   - All other relevant information that can be found in the database

### Process for Adding New Sources
1. Follow the exact same patterns as existing crawlers in the codebase
2. Use consistent data structures for listings and fields
3. Ensure proper error handling to prevent crawl failures
4. Add to the main crawler list in the correct alphabetical location
5. Update README.md with any new features or changes

### Example Implementation Pattern
```python
def crawl_new_source(session):
    # Implementation following existing conventions
    pass
```

## Testing and Validation
- All crawlers should work independently and integrate properly with the database
- Generated houses.html should display all listings correctly
- The update_all.sh script should execute successfully without errors

## Environment Requirements
- Python 3.10+ required
- Virtual environment (.venv) must be active for proper execution
- requirements.txt contains all necessary dependencies