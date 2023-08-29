import { Example } from "./Example";

import styles from "./Example.module.css";

export type ExampleModel = {
    text: string;
    value: string;
};

const EXAMPLES: ExampleModel[] = [
    {
        text: "Was ist bei der Nexible Reiserücktrittsversicherung alles abgedeckt?",
        value: "Was ist bei der Nexible Reiserücktrittsversicherung alles abgedeckt?"
    },
    {
        text: "Welche Zahnzusatz-Tarife gibt es?",
        value: "Welche Zahnzusatz-Tarife gibt es?"
    },
    {
        text: "Wie kann ich Zahnarztrechnungen einreichen?",
        value: "Wie kann ich Zahnarztrechnungen einreichen?"
    }
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
