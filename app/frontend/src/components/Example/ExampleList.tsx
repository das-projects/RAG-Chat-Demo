import { Example } from "./Example";

import styles from "./Example.module.css";

export type ExampleModel = {
    text: string;
    value: string;
};

const EXAMPLES: ExampleModel[] = [
    {
        text: "Was ist bei der ERGO E-Bike Versicherung alles abgedeckt?",
        value: "Was ist bei der ERGO E-Bike Versicherung alles abgedeckt?"
    },
    { text: "Sind Reitbeteiligungen in meiner Pferdehaftpflichtversicherung mitversichert?", value: "Sind Reitbeteiligungen in meiner Pferdehaftpflichtversicherung mitversichert?" },
    { text: "Sind Abschleppkosten in meiner KFZ Haftpflichtversicherung enthalten?", value: "Sind Abschleppkosten in meiner KFZ Haftpflichtversicherung enthalten?" }
];

interface Props {
    onExampleClicked: (value: string) => void;
}

export const ExampleList = ({ onExampleClicked }: Props) => {
    return (
        <ul className={styles.examplesNavList}>
            {EXAMPLES.map((x, i) => (
                <li key={i}>
                    <Example text={x.text} value={x.value} onClick={onExampleClicked} />
                </li>
            ))}
        </ul>
    );
};
