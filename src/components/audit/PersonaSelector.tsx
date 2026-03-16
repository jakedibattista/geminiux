'use client';
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export type Persona = {
  id: string;
  name: string;
  description: string;
  goals: string[];
  deviceType: 'mobile' | 'desktop';
};

export const defaultPersonas: Persona[] = [
  {
    id: 'p_first_time',
    name: 'First-Time Visitor',
    description: 'A user seeing the site for the very first time. They have zero context and low patience.',
    goals: ['Understand what the site does within 5 seconds', 'Find clear pricing', 'Identify how to get started easily'],
    deviceType: 'desktop',
  },
  {
    id: 'p_mobile',
    name: 'Mobile User',
    description: 'A user browsing on a smartphone, potentially with one hand, while on the go.',
    goals: ['Navigate without zooming', 'Click links easily with a thumb', 'Avoid horizontal scrolling'],
    deviceType: 'mobile',
  },
  {
    id: 'p_accessibility',
    name: 'Accessibility User',
    description: 'A user who relies on clear contrast, readable fonts, and keyboard-friendly navigation.',
    goals: ['Read all text without eye strain', 'Navigate forms using only the keyboard', 'Understand links without relying purely on color'],
    deviceType: 'desktop',
  },
  {
    id: 'p_non_technical',
    name: 'Non-Technical User',
    description: 'A user who gets easily confused by industry jargon and complex UI patterns.',
    goals: ['Complete a task without feeling overwhelmed', 'Avoid error messages', 'Find a simple contact/help button'],
    deviceType: 'desktop',
  },
  {
    id: 'p_power_user',
    name: 'Power User',
    description: 'An advanced user who wants to get things done quickly and efficiently.',
    goals: ['Find advanced settings or filters quickly', 'Use keyboard shortcuts (if applicable)', 'Skip tutorial/onboarding steps'],
    deviceType: 'desktop',
  }
];

export function PersonaSelector({ 
  selectedIds, 
  onChange,
  customPersonas = [],
  onDeleteCustom,
  onEditCustom,
}: { 
  selectedIds: string[], 
  onChange: (ids: string[]) => void,
  customPersonas?: Persona[],
  onDeleteCustom?: (id: string) => void,
  onEditCustom?: (persona: Persona) => void,
}) {
  const togglePersona = (id: string) => {
    if (selectedIds.includes(id)) {
      onChange(selectedIds.filter(prevId => prevId !== id));
    } else {
      onChange([...selectedIds, id]);
    }
  };

  const allPersonas = [...defaultPersonas, ...customPersonas];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {allPersonas.map((persona) => {
          const isSelected = selectedIds.includes(persona.id);
          const isCustom = persona.id.startsWith('p_custom_');
          return (
            <Card 
              key={persona.id}
              className={`cursor-pointer transition-all border-2 ${isSelected ? 'border-primary bg-primary/5' : 'border-transparent hover:border-border'}`}
              onClick={() => togglePersona(persona.id)}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-lg leading-tight">{persona.name}</CardTitle>
                <CardAction className="flex flex-col items-end gap-2 pl-3">
                  <Badge variant={persona.deviceType === 'mobile' ? 'secondary' : 'outline'} className="shrink-0">
                    {persona.deviceType}
                  </Badge>
                  {isCustom && (onEditCustom || onDeleteCustom) ? (
                    <div className="flex items-center gap-1">
                      {onEditCustom && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onEditCustom(persona);
                          }}
                          className="rounded-full p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                          title="Edit persona"
                          aria-label={`Edit ${persona.name}`}
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"/>
                          </svg>
                        </button>
                      )}
                      {onDeleteCustom && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteCustom(persona.id);
                          }}
                          className="rounded-full p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                          title="Delete persona"
                          aria-label={`Delete ${persona.name}`}
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M18 6 6 18"/><path d="m6 6 12 12"/>
                          </svg>
                        </button>
                      )}
                    </div>
                  ) : null}
                </CardAction>
                <CardDescription className="text-sm leading-relaxed">{persona.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-1">
                  {persona.goals.map((goal, i) => (
                    <li key={i} className="leading-snug" title={goal}>{goal}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}