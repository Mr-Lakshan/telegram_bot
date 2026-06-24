"""
DATABASE SIMULATOR - PHASE 1
Mock database for testing (will be replaced with real DB in Phase 2)
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

class MockDatabase:
    """
    Simulates project/customer database for testing
    This will be replaced with real database queries in Phase 2
    """
    
    def __init__(self):
        # Sample projects
        self.projects = {
            'seidel_bathroom': {
                'project_id': 'PRJ-2024-089',
                'customer_name': 'Seidel Family',
                'customer_primary': 'Waltraut Seidel',
                'customer_id': 'N310371391',
                'address': 'Hauptstraße 45, 50667 Köln',
                'project_type': 'Bathroom Renovation',
                'start_date': '2024-10-06',
                'status': 'in_progress',
                'specs': {
                    'shower_dimensions': '90×120 cm',
                    'glass_wall': '120 cm',
                    'materials': {
                        'pipes': 'Copper + PEX',
                        'panels': 'Custom wall panels',
                        'drain': 'Linear drain'
                    }
                },
                'contact_preference': 'Daughter (main contact)',
                'special_notes': 'Washing machine needs to be moved from basement to bathroom'
            }
        }
        
        # Sample customers
        self.customers = {
            'N310371391': {
                'name': 'Waltraut Seidel',
                'partner': 'Heinz Seidel',
                'partner_id': 'N648445612',
                'phone': '+49 221 1234567',
                'email': 'seidel@example.com',
                'primary_contact': 'Daughter',
                'language': 'German',
                'availability': 'Not available Tuesday-Friday this week'
            }
        }
        
        # Sample schedule
        today = datetime.now()
        self.schedule = [
            {
                'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
                'worker': 'Piotr',
                'project': 'seidel_bathroom',
                'task': 'Glass wall installation',
                'time': '08:00',
                'duration': '4 hours',
                'status': 'scheduled'
            },
            {
                'date': (today + timedelta(days=3)).strftime('%Y-%m-%d'),
                'worker': 'Lukasz',
                'project': 'seidel_bathroom',
                'task': 'Final inspection',
                'time': '10:00',
                'duration': '2 hours',
                'status': 'scheduled'
            }
        ]
        
        # Sample past issues (for learning)
        self.past_issues = [
            {
                'issue_type': 'space_conflict',
                'description': 'Appliance blocking installation',
                'solution': 'Move appliance first, then install fixture',
                'project': 'schmidt_kitchen',
                'cost': 0,
                'success': True
            },
            {
                'issue_type': 'panel_gap',
                'description': 'Gap between wall panels and tiles',
                'solution': 'Cover strip or new panels',
                'project': 'mueller_bathroom',
                'cost': 1800,
                'success': True
            },
            {
                'issue_type': 'drainage',
                'description': 'Poor water drainage',
                'solution': 'Clean drain, check slope',
                'project': 'weber_shower',
                'cost': 150,
                'success': True
            }
        ]
        
        # Worker information
        self.workers = {
            'Piotr': {
                'role': 'worker',
                'specialty': 'Plumbing, installations',
                'language': 'Polish, limited German',
                'availability': 'Monday-Friday 7am-4pm'
            },
            'Lukasz': {
                'role': 'worker',
                'specialty': 'Finishing, quality control',
                'language': 'Polish, German',
                'availability': 'Monday-Saturday 8am-5pm'
            },
            'Weronika': {
                'role': 'coordinator',
                'specialty': 'Scheduling, customer communication',
                'language': 'Polish, German, English',
                'availability': 'Monday-Friday 8am-6pm'
            },
            'Yevhenniatop': {
                'role': 'manager',
                'specialty': 'Project management, customer relations',
                'language': 'German, English',
                'availability': 'Monday-Friday 9am-6pm'
            },
            'Lothar': {
                'role': 'boss',
                'specialty': 'Final decisions, financial approval',
                'language': 'German',
                'availability': 'As needed'
            }
        }
    
    def find_project(self, customer_name: str = None, project_id: str = None) -> Optional[Dict]:
        """Find project by customer name or project ID"""
        if project_id:
            for proj in self.projects.values():
                if proj['project_id'] == project_id:
                    return proj
        
        if customer_name:
            customer_lower = customer_name.lower()
            for proj in self.projects.values():
                if customer_lower in proj['customer_name'].lower():
                    return proj
        
        return None
    
    def find_customer(self, name: str = None, customer_id: str = None) -> Optional[Dict]:
        """Find customer by name or ID"""
        if customer_id and customer_id in self.customers:
            return self.customers[customer_id]
        
        if name:
            name_lower = name.lower()
            for customer in self.customers.values():
                if name_lower in customer['name'].lower():
                    return customer
        
        return None
    
    def get_schedule(self, worker: str = None, date: str = None, project: str = None) -> List[Dict]:
        """Get schedule filtered by worker, date, or project"""
        results = self.schedule.copy()
        
        if worker:
            results = [s for s in results if s['worker'].lower() == worker.lower()]
        
        if date:
            results = [s for s in results if s['date'] == date]
        
        if project:
            results = [s for s in results if s['project'] == project]
        
        return results
    
    def search_similar_issues(self, issue_type: str = None, keywords: str = None) -> List[Dict]:
        """Search past issues for similar problems"""
        results = []
        
        for issue in self.past_issues:
            match = False
            
            if issue_type and issue_type.lower() in issue['issue_type'].lower():
                match = True
            
            if keywords:
                keywords_lower = keywords.lower()
                if (keywords_lower in issue['description'].lower() or 
                    keywords_lower in issue['solution'].lower()):
                    match = True
            
            if match:
                results.append(issue)
        
        return results
    
    def get_worker_info(self, name: str) -> Optional[Dict]:
        """Get worker information"""
        for worker_name, info in self.workers.items():
            if worker_name.lower() == name.lower():
                return info
        return None
    
    def query_context(self, entities: Dict) -> Dict:
        """
        Main function to gather all relevant context based on extracted entities
        This simulates what will be complex database queries in Phase 2
        """
        context = {}
        
        # Find project
        if entities.get('customer_name') or entities.get('project_name'):
            project = self.find_project(
                customer_name=entities.get('customer_name'),
                project_id=entities.get('project_name')
            )
            if project:
                context['project'] = project
                
                # Also get customer info
                customer = self.find_customer(customer_id=project['customer_id'])
                if customer:
                    context['customer'] = customer
        
        # Get schedule
        if entities.get('date'):
            context['schedule'] = self.get_schedule(date=entities['date'])
        
        # Search for similar past issues
        if entities.get('problem_type'):
            context['similar_issues'] = self.search_similar_issues(
                issue_type=entities['problem_type']
            )
        
        # Add default values if nothing found
        if not context:
            context['note'] = 'No specific project/customer data found in query'
        
        return context


# Example usage
if __name__ == "__main__":
    db = MockDatabase()
    
    print("\n🗄️  DATABASE SIMULATOR TEST\n")
    print("="*60)
    
    # Test 1: Find project
    print("\n1. Finding Seidel project:")
    project = db.find_project(customer_name="Seidel")
    print(json.dumps(project, indent=2))
    
    # Test 2: Find customer
    print("\n2. Finding customer:")
    customer = db.find_customer(name="Waltraut")
    print(json.dumps(customer, indent=2))
    
    # Test 3: Get schedule
    print("\n3. Getting Piotr's schedule:")
    schedule = db.get_schedule(worker="Piotr")
    print(json.dumps(schedule, indent=2))
    
    # Test 4: Search similar issues
    print("\n4. Searching for panel gap issues:")
    issues = db.search_similar_issues(keywords="panel gap")
    print(json.dumps(issues, indent=2))
    
    # Test 5: Query context (what will be used in main system)
    print("\n5. Query context for entities:")
    entities = {
        'customer_name': 'Seidel',
        'problem_type': 'space_conflict'
    }
    context = db.query_context(entities)
    print(json.dumps(context, indent=2))
    
    print("\n✅ Database simulator working correctly!")