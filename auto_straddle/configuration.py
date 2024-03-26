"""Module providing a function for main function """

import pandas as pd

class ConfigurationLoader:
    configuration = None

    @staticmethod
    def load_configuration():
        url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vShIRDWl4z12XqBqnHwbi67adbHCO7ibdMz3XUc_JsxwhKQTG03LLe2ZleVqcyrKnX5J7YFPan6qqFI/pub?output=csv"
        df = pd.read_csv(url)

        # Convert the dataframe to a dictionary
        #ConfigurationLoader.configuration = account_details.to_dict('split')
        ConfigurationLoader.configuration = dict(df.values)

    @staticmethod
    def get_configuration():
        if ConfigurationLoader.configuration is None:
            ConfigurationLoader.load_configuration()

        return ConfigurationLoader.configuration

'''
# Test the ConfigurationLoader class
if __name__ == "__main__":
    #configuration_data = ConfigurationLoader.get_configuration()
    #print(configuration_data)
    #print(configuration_data['deepti_telegram'])
    print(ConfigurationLoader.get_configuration()['deepti_telegram'])
'''
